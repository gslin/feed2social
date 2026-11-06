"""Offline integration checks using fbpost's real payload and response helpers."""

import configparser
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import feedparser
from curl_cffi.requests import RequestsError, Response, Session
from scripts.fb_config import POST_DOC_ID

from feed2facebook import Feed2Facebook


class Feed2FacebookTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.enterContext(patch.dict('os.environ', {'HOME': str(self.home)}))
        config_dir = self.home / '.config/feed2social'
        config_dir.mkdir(parents=True)
        self.db_path = config_dir / 'feed2facebook.sqlite3'
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute('CREATE TABLE entry (entry_id VARCHAR, created_at INT)')
        self.cookie_file = config_dir / 'facebook-cookies.json'
        self.cookies = [
            {'name': 'c_user', 'value': '100001', 'domain': '.facebook.com'},
            {'name': 'xs', 'value': 'private-session', 'domain': '.facebook.com'},
        ]
        self.write_cookies()
        self.app = Feed2Facebook()
        self.app._config = configparser.ConfigParser()
        self.app._config.read_dict({'default': {'feed_url': 'https://example.com/feed'}})
        self.items = [self.entry()]
        self.enterContext(patch(
            'feed2facebook.feedparser.parse',
            return_value=SimpleNamespace(entries=self.items),
        ))
        self.enterContext(patch('feed2facebook.tprint'))
        self.enterContext(patch(
            'curl_cffi.Curl.perform', side_effect=AssertionError('Unexpected network access'),
        ))
        self.http = Mock()
        # curl_cffi binds get/post with partialmethod, so patch those entry points.
        def request(session, url, **kwargs):
            return self.http(session, url, **kwargs)
        self.enterContext(patch.object(Session, 'get', request))
        self.enterContext(patch.object(Session, 'post', request))
        self.http.side_effect = [self.homepage(), self.post_response()]

    @staticmethod
    def entry(body='<p>Hello &amp; 台灣</p>', entry_id='entry-1'):
        return feedparser.FeedParserDict(
            id=entry_id, description=body, link='https://example.com/post',
        )

    def write_cookies(self):
        self.cookie_file.write_text(json.dumps(self.cookies), encoding='utf-8')

    @staticmethod
    def response(body, status=200):
        response = Response()
        response.content = body.encode('utf-8')
        response.status_code = status
        response.ok = 200 <= status < 400
        return response

    @classmethod
    def homepage(cls):
        return cls.response(
            '<html>"DTSGInitData",[],{"token":"private-csrf"}'
            '"LSD",[],{"token":"private-lsd"} jazoest=12345 '
            '"client_revision":123,"hsi":"456","__spin_t":789</html>'
        )

    @classmethod
    def post_response(cls):
        return cls.response(json.dumps({
            'data': {'story_create': {'story': {
                'id': 'story-1', 'url': 'https://www.facebook.com/posts/story-1',
            }}},
        }))

    def recorded_ids(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            return [row[0] for row in db.execute('SELECT entry_id FROM entry ORDER BY entry_id')]

    def posted_form(self):
        return self.http.call_args.kwargs['data']

    def test_posts_with_fbpost_and_impersonation_then_skips_processed_entries(self):
        self.app.main()
        self.app.main()

        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 2)
        session = self.http.call_args.args[0]
        self.assertEqual(session.impersonate, 'chrome124')
        self.assertFalse(session.allow_redirects)
        self.assertTrue(session._closed)
        self.assertEqual(session.cookies['xs'], 'private-session')
        self.assertTrue(all(cookie.secure and cookie.domain == '.facebook.com'
                            for cookie in session.cookies.jar))
        self.assertEqual(self.http.call_args.args[1], 'https://www.facebook.com/api/graphql/')
        form = self.posted_form()
        self.assertEqual(form['doc_id'], POST_DOC_ID)
        self.assertEqual(form['fb_dtsg'], 'private-csrf')
        self.assertEqual(form['lsd'], 'private-lsd')
        self.assertEqual(form['av'], '100001')
        variables = json.loads(form['variables'])
        self.assertEqual(variables['input']['message']['text'],
                         'Hello & 台灣\n\nhttps://example.com/post')
        self.assertEqual(variables['input']['audience']['privacy']['base_state'], 'EVERYONE')
        self.assertTrue(variables['input']['idempotence_token'])

    def test_cookie_path_page_identity_privacy_and_doc_id_can_be_configured(self):
        self.cookies.append({'name': 'i_user', 'value': '200002', 'domain': '.facebook.com'})
        self.write_cookies()
        other_path = self.home / 'page.json'
        self.cookie_file.rename(other_path)
        self.app._config['default'].update(
            facebook_cookies_file='~/page.json', facebook_privacy='SELF',
            facebook_post_doc_id='123456789',
        )

        self.app.main()

        form = self.posted_form()
        self.assertEqual(form['doc_id'], '123456789')
        self.assertEqual(form['av'], '200002')
        variables = json.loads(form['variables'])
        self.assertEqual(variables['input']['actor_id'], '200002')
        self.assertEqual(variables['input']['audience']['privacy']['base_state'], 'SELF')
        self.assertEqual(self.http.call_args.args[0].cookies['c_user'], '100001')

    def test_sync_only_needs_no_cookies_and_never_contacts_facebook(self):
        self.items.insert(0, self.entry(entry_id='entry-2'))
        self.cookie_file.unlink()
        self.app.main(sync_only=True)
        self.assertEqual(self.recorded_ids(), ['entry-1', 'entry-2'])
        self.http.assert_not_called()

    def test_excluded_and_processed_entries_need_no_cookies(self):
        self.app.main(sync_only=True)
        self.cookie_file.unlink()
        self.items.insert(0, self.entry(body='#nofb', entry_id='entry-2'))
        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.http.assert_not_called()

    def test_missing_or_malformed_cookies_stop_before_network_access(self):
        for body in ('invalid JSON', '{}', '[null]', '[{"name":"xs"}]', '[]'):
            with self.subTest(body=body):
                self.cookie_file.write_text(body, encoding='utf-8')
                with self.assertRaises(configparser.Error):
                    self.app.main()
        self.cookie_file.unlink()
        with self.assertRaises(configparser.Error):
            self.app.main()
        self.http.assert_not_called()
        self.assertEqual(self.recorded_ids(), [])

    def test_other_domains_cannot_supply_or_override_facebook_cookies(self):
        self.cookies.append({'name': 'xs', 'value': 'wrong-session', 'domain': 'notfacebook.com'})
        self.write_cookies()
        self.assertEqual(self.app.load_cookies()['xs'], 'private-session')
        self.cookies[0]['domain'] = 'facebook.com.example.com'
        self.write_cookies()
        with self.assertRaisesRegex(configparser.Error, 'c_user and xs'):
            self.app.main()
        self.http.assert_not_called()

    def test_invalid_privacy_or_document_id_stops_before_network_access(self):
        for key, value in (('facebook_privacy', 'ANYONE'), ('facebook_post_doc_id', 'bad')):
            with self.subTest(key=key):
                self.app._config['default'][key] = value
                with self.assertRaises(configparser.Error):
                    self.app.main()
                del self.app._config['default'][key]
        self.http.assert_not_called()

    def test_expired_cookies_stop_without_posting_and_close_session(self):
        self.http.side_effect = [self.response('<html>Log in</html>')]
        with self.assertRaisesRegex(RuntimeError, 'fetch login tokens'):
            self.app.main()
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self.recorded_ids(), [])
        self.assertTrue(self.http.call_args.args[0]._closed)

    def test_http_failures_do_not_leak_responses_record_entries_or_retry(self):
        self.items.insert(0, self.entry(entry_id='entry-2'))
        for status in (302, 401, 403, 429, 500):
            with self.subTest(status=status):
                self.http.reset_mock()
                self.http.side_effect = [
                    self.homepage(), self.response('private-response-body', status),
                ]
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output):
                    with self.assertRaisesRegex(RuntimeError, 'publish the post'):
                        self.app.main()
                self.assertEqual(output.getvalue(), '')
                self.assertEqual(self.recorded_ids(), [])
                self.assertEqual(self.http.call_count, 2)
                self.assertTrue(self.http.call_args.args[0]._closed)

    def test_graphql_errors_and_missing_story_ids_are_not_recorded(self):
        invalid_results = [
            {'errors': [{'message': 'Request rejected'}]},
            {'data': None}, {}, [], {'data': {'story_create': None}},
            {'data': {'story_create': {'story': {}}}},
            {'data': {'story_create': {'story': {'id': True, 'url': ''}}}},
        ]
        for result in invalid_results:
            with self.subTest(result=result):
                self.http.side_effect = [self.homepage(), self.response(json.dumps(result))]
                with self.assertRaises(RuntimeError):
                    self.app.main()
                self.assertEqual(self.recorded_ids(), [])

    def test_unparseable_response_is_not_logged_or_recorded(self):
        self.http.side_effect = [self.homepage(), self.response('<html>private-response-body</html>')]
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            with self.assertRaisesRegex(RuntimeError, 'publish the post'):
                self.app.main()
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(self.recorded_ids(), [])

    def test_timeout_is_not_retried_and_does_not_record_entry(self):
        self.http.side_effect = [self.homepage(), RequestsError('Timed out')]
        with self.assertRaises(RequestsError):
            self.app.main()
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(self.recorded_ids(), [])
        self.assertTrue(self.http.call_args.args[0]._closed)

    def test_posts_one_entry_per_run_in_feed_order(self):
        self.items.insert(0, self.entry(body='<p>Second post</p>', entry_id='entry-2'))
        self.items.append(self.entry(body='#nofb', entry_id='excluded'))
        self.http.side_effect = [
            self.homepage(), self.post_response(), self.homepage(), self.post_response(),
        ]

        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(json.loads(self.posted_form()['variables'])['input']['message']['text'],
                         'Hello & 台灣\n\nhttps://example.com/post')

        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1', 'entry-2'])
        self.assertEqual(self.http.call_count, 4)
        self.assertEqual(json.loads(self.posted_form()['variables'])['input']['message']['text'],
                         'Second post\n\nhttps://example.com/post')

        self.app.main()
        self.assertEqual(self.http.call_count, 4)

    def test_failure_on_next_run_preserves_successful_entry_and_feed_order(self):
        self.items.insert(0, self.entry(body='<p>Second post</p>', entry_id='entry-2'))
        self.http.side_effect = [
            self.homepage(), self.post_response(),
            self.homepage(), self.response('{"errors":[{"message":"Rejected"}]}'),
        ]

        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 2)

        with self.assertRaises(RuntimeError):
            self.app.main()

        self.assertEqual(self.recorded_ids(), ['entry-1'])
        posts = [call for call in self.http.call_args_list if 'data' in call.kwargs]
        self.assertEqual(len(posts), 2)
        self.assertEqual(json.loads(posts[0].kwargs['data']['variables'])['input']['message']['text'],
                         'Hello & 台灣\n\nhttps://example.com/post')
        self.assertEqual(json.loads(posts[1].kwargs['data']['variables'])['input']['message']['text'],
                         'Second post\n\nhttps://example.com/post')

    def test_upstream_handles_json_prefix_and_streamed_responses(self):
        payload = self.post_response().text
        for body in ('for (;;);' + payload, '{}\n' + payload + '\n{"extensions":{}}'):
            with self.subTest(body=body):
                self.http.side_effect = [self.homepage(), self.response(body)]
                self.assertEqual(self.app.post('Hello'), 'story-1')

    def test_url_only_confirmation_is_accepted(self):
        self.http.side_effect = [self.homepage(), self.response(json.dumps({
            'data': {'story_create': {'story': {'url': 'https://www.facebook.com/posts/1'}}},
        }))]
        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])


if __name__ == '__main__':
    unittest.main()
