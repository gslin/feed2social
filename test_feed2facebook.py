"""Offline integration checks using fbpost's real payload and response helpers."""

import base64
import configparser
import io
import json
import runpy
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import feedparser
from curl_cffi.requests import RequestsError, Response, Session
from scripts.fb_config import POST_DOC_ID, REPLY_DOC_ID
from scripts.fb_session import REPLY_RELAY_PV_FLAGS

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
        self.http.side_effect = [
            self.homepage(), self.post_response(), self.homepage(), self.comment_response(),
        ]

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
                'id': '12345678901234567',
                'url': 'https://www.facebook.com/posts/12345678901234567',
            }}},
        }))

    @classmethod
    def comment_response(cls):
        return cls.response(json.dumps({
            'data': {'comment_create': {'feedback_comment_edge': {'node': {'id': 'comment-1'}}}},
        }))

    def recorded_ids(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            return [row[0] for row in db.execute('SELECT entry_id FROM entry ORDER BY entry_id')]

    def posted_form(self, operation='ComposerStoryCreateMutation'):
        for request in reversed(self.http.call_args_list):
            form = request.kwargs.get('data', {})
            if form.get('fb_api_req_friendly_name') == operation:
                return form
        self.fail(f'No request for {operation}')

    def test_posts_with_fbpost_and_impersonation_then_skips_processed_entries(self):
        self.app.main()
        self.app.main()

        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 4)
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
                         'Hello & 台灣')
        self.assertEqual(variables['input']['audience']['privacy']['base_state'], 'EVERYONE')
        self.assertTrue(variables['input']['idempotence_token'])
        comment = self.posted_form('useCometUFICreateCommentMutation')
        self.assertEqual(comment['doc_id'], REPLY_DOC_ID)
        self.assertEqual(comment['av'], '100001')
        variables = json.loads(comment['variables'])
        self.assertEqual(variables['input']['message'], {
            'ranges': [], 'text': 'Sync from: https://example.com/post',
        })
        self.assertEqual(base64.b64decode(variables['input']['feedback_id']).decode(),
                         'feedback:12345678901234567')
        self.assertIsNone(variables['input']['reply_comment_parent_fbid'])
        self.assertFalse(variables['input']['reply_target_clicked'])
        self.assertIsNone(variables['focusCommentID'])
        for key, value in REPLY_RELAY_PV_FLAGS.items():
            self.assertEqual(variables[key], value)

    def test_cookie_path_page_identity_privacy_and_doc_id_can_be_configured(self):
        self.cookies.append({'name': 'i_user', 'value': '200002', 'domain': '.facebook.com'})
        self.write_cookies()
        other_path = self.home / 'page.json'
        self.cookie_file.rename(other_path)
        self.app._config['default'].update(
            facebook_cookies_file='~/page.json', facebook_privacy='SELF',
            facebook_post_doc_id='123456789', facebook_comment_doc_id='987654321',
        )

        self.app.main()

        form = self.posted_form()
        self.assertEqual(form['doc_id'], '123456789')
        self.assertEqual(form['av'], '200002')
        variables = json.loads(form['variables'])
        self.assertEqual(variables['input']['actor_id'], '200002')
        self.assertEqual(variables['input']['audience']['privacy']['base_state'], 'SELF')
        self.assertEqual(self.http.call_args.args[0].cookies['c_user'], '100001')
        comment = self.posted_form('useCometUFICreateCommentMutation')
        self.assertEqual(comment['doc_id'], '987654321')
        self.assertEqual(comment['av'], '200002')
        self.assertEqual(json.loads(comment['variables'])['input']['actor_id'], '200002')

    def test_sync_only_needs_no_cookies_and_never_contacts_facebook(self):
        self.items.insert(0, self.entry(entry_id='entry-2'))
        self.app._config['default'].update(
            facebook_c_user='incomplete-config', facebook_comment_doc_id='invalid',
        )
        self.cookie_file.unlink()
        self.app.main(sync_only=True, debug=True)
        self.assertEqual(self.recorded_ids(), ['entry-1', 'entry-2'])
        self.http.assert_not_called()

    def test_ini_cookies_preserve_percent_escapes_and_take_precedence_over_file(self):
        config_file = self.cookie_file.parent / 'config.ini'
        config_file.write_text(
            '[default]\nfeed_url = https://example.com/feed\n'
            'facebook_c_user = 300003\n'
            'facebook_xs = 23%3Aprivate-session%3A2%3A12345\n',
            encoding='utf-8',
        )
        self.app._config = None
        self.cookie_file.write_text('invalid JSON', encoding='utf-8')

        self.app.main()

        session = self.http.call_args.args[0]
        self.assertEqual(session.cookies['c_user'], '300003')
        self.assertEqual(session.cookies['xs'], '23%3Aprivate-session%3A2%3A12345')
        self.assertEqual(self.posted_form()['av'], '300003')
        self.assertEqual(self.recorded_ids(), ['entry-1'])

    def test_ini_supports_page_identity_and_optional_cookies_without_file(self):
        self.app._config['default'].update(
            facebook_c_user='300003', facebook_xs='ini-session', facebook_i_user='400004',
            facebook_datr='device-cookie', facebook_sb='browser-cookie', facebook_fr='fr-cookie',
            facebook_presence='',
        )
        self.cookie_file.unlink()

        self.app.main()

        session = self.http.call_args.args[0]
        self.assertEqual(session.cookies['c_user'], '300003')
        self.assertEqual(session.cookies['i_user'], '400004')
        self.assertEqual(session.cookies['datr'], 'device-cookie')
        self.assertEqual(session.cookies['sb'], 'browser-cookie')
        self.assertEqual(session.cookies['fr'], 'fr-cookie')
        self.assertNotIn('presence', session.cookies)
        self.assertEqual(self.posted_form()['av'], '400004')

    def test_incomplete_ini_cookies_do_not_fall_back_to_valid_cookie_file(self):
        for settings in (
            {'facebook_c_user': '300003'},
            {'facebook_xs': 'ini-session'},
            {'facebook_i_user': '400004'},
            {'facebook_c_user': '', 'facebook_xs': ''},
            {'facebook_c_user': '300003', 'facebook_xs': '   '},
        ):
            with self.subTest(settings=settings):
                self.app._config['default'].update(settings)
                with self.assertRaisesRegex(configparser.Error, 'facebook_c_user and facebook_xs'):
                    self.app.main()
                for key in settings:
                    del self.app._config['default'][key]
        self.http.assert_not_called()
        self.assertEqual(self.recorded_ids(), [])

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
        for key, value in (('facebook_privacy', 'ANYONE'), ('facebook_post_doc_id', 'bad'),
                           ('facebook_comment_doc_id', 'bad')):
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

    def test_login_failure_reports_metadata_without_secrets_or_doc_id_advice(self):
        for status in (200, 302):
            with self.subTest(status=status):
                response = self.response('<html>private-page-content</html>', status)
                response.headers['Content-Type'] = 'text/html; charset=utf-8'
                response.headers['Set-Cookie'] = 'xs=private-response-cookie'
                if status == 302:
                    response.headers['Location'] = '/login/?next=private-redirect-value'
                self.http.reset_mock()
                self.http.side_effect = [response]
                output = io.StringIO()
                with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
                    with self.assertRaises(RuntimeError) as raised:
                        self.app.main()
                log = output.getvalue()
                self.assertIn(f'HTTP {status}, content-type="text/html"', log)
                self.assertIn(f'redirect={status == 302}', log)
                self.assertIn('"DTSGInitData": false', log)
                self.assertIn('fb_dtsg extracted = False', log)
                message = str(raised.exception)
                self.assertIn('--check-login', message)
                self.assertIn('No post was sent', message)
                self.assertNotIn('facebook_post_doc_id', message)
                for secret in ('private-page-content', 'private-response-cookie',
                               'private-redirect-value', 'private-session'):
                    self.assertNotIn(secret, log + message)
                self.assertEqual(self.http.call_count, 1)
                self.assertEqual(self.recorded_ids(), [])
                self.assertTrue(self.http.call_args.args[0]._closed)

    def test_unrecognized_token_markup_reports_marker_without_guessing_a_token(self):
        self.http.side_effect = [self.response(
            '<html>"DTSGInitData", [], {"token":"private-csrf"}</html>',
        )]
        output = io.StringIO()
        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'fetch login tokens'):
                self.app.main(debug=True)
        self.assertIn('"DTSGInitData": true', output.getvalue())
        self.assertIn('"DTSGInitData": [{"token": "<string: 12 chars>"}]', output.getvalue())
        self.assertIn('fb_dtsg extracted = False', output.getvalue())
        self.assertNotIn('private-csrf', output.getvalue())
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self.recorded_ids(), [])

    def test_login_diagnostics_distinguish_missing_empty_and_null_tokens(self):
        for token_fields in ({}, {'token': ''}, {'token': None}, {'token': False}):
            with self.subTest(token_fields=token_fields):
                self.http.reset_mock()
                self.http.side_effect = [self.response(
                    '<html>["DTSGInitData", [], ' + json.dumps(token_fields) + ', 258],'
                    '["LSD",[],{"token":"private-lsd"},323]</html>',
                )]
                output = io.StringIO()
                with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
                    with self.assertRaisesRegex(RuntimeError, 'fetch login tokens'):
                        self.app.main(check_login=True)
                log = output.getvalue()
                expected = dict(token_fields)
                if expected.get('token') == '':
                    expected['token'] = '<string: 0 chars>'
                self.assertIn('"DTSGInitData": ' + json.dumps([expected]), log)
                self.assertIn('"LSD": [{"token": "<string: 11 chars>"}]', log)
                self.assertNotIn('private-lsd', log)
                self.assertEqual(self.http.call_count, 1)
                self.assertEqual(self.recorded_ids(), [])

    def test_login_diagnostics_redact_reordered_fields_and_skip_unrelated_values(self):
        self.http.side_effect = [self.response(
            '<html>["DTSGInitData",[],{"private-key":"private-value",'
            '"async_get_token":"private-async","token":"private-csrf"},258]</html>',
        )]
        output = io.StringIO()
        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'fetch login tokens'):
                self.app.main(check_login=True)
        log = output.getvalue()
        self.assertIn('"token": "<string: 12 chars>"', log)
        self.assertIn('"async_get_token": "<string: 13 chars>"', log)
        for secret in ('private-key', 'private-value', 'private-async', 'private-csrf'):
            self.assertNotIn(secret, log)
        self.assertEqual(self.http.call_count, 1)

    def test_login_diagnostics_handle_invalid_module_json_without_leaking_it(self):
        for payload in ('{"secret":"private-value"', '"private-value"', '["private-value"]',
                        '{"token":{"private-value":"private-value"}}'):
            with self.subTest(payload=payload):
                self.http.side_effect = [self.response(
                    '<html>["DTSGInitData",[],' + payload + ']</html>',
                )]
                output = io.StringIO()
                with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
                    with self.assertRaisesRegex(RuntimeError, 'fetch login tokens'):
                        self.app.main(check_login=True)
                self.assertNotIn('private-value', output.getvalue())
                self.assertIn('token fields', output.getvalue())

    def test_login_http_error_and_timeout_report_diagnostics_without_posting(self):
        for response in (self.response('private-error-page', 503), RequestsError('Timed out')):
            with self.subTest(response=response):
                self.http.reset_mock()
                self.http.side_effect = [response]
                output = io.StringIO()
                with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
                    with self.assertRaises(RequestsError):
                        self.app.main()
                expected = 'HTTP 503' if isinstance(response, Response) else 'no HTTP response received'
                self.assertIn(expected, output.getvalue())
                self.assertNotIn('private-error-page', output.getvalue())
                self.assertEqual(self.http.call_count, 1)
                self.assertEqual(self.recorded_ids(), [])
                self.assertTrue(self.http.call_args.args[0]._closed)

    def test_check_login_only_fetches_tokens_without_feed_database_or_post_settings(self):
        self.db_path.unlink()
        del self.app._config['default']['feed_url']
        self.app._config['default'].update(
            facebook_privacy='INVALID', facebook_post_doc_id='INVALID',
            facebook_comment_doc_id='INVALID',
        )
        self.http.side_effect = [self.homepage()]
        output = io.StringIO()
        with (patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output),
              patch('feed2facebook.feedparser.parse') as parse,
              patch('feed2facebook.sqlite3.connect') as connect):
            self.app.main(check_login=True)
        parse.assert_not_called()
        connect.assert_not_called()
        self.assertFalse(self.db_path.exists())
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self.http.call_args.args[1], 'https://www.facebook.com/')
        self.assertNotIn('data', self.http.call_args.kwargs)
        self.assertTrue(self.http.call_args.args[0]._closed)
        log = output.getvalue()
        self.assertIn('fb_dtsg extracted = True', log)
        self.assertIn('login-token check passed; posting was not tested', log)
        for secret in ('private-session', 'private-csrf', 'private-lsd'):
            self.assertNotIn(secret, log)

    def test_failed_check_login_does_not_touch_feed_or_database(self):
        self.http.side_effect = [self.response('<html>Log in</html>')]
        with (patch('feed2facebook.feedparser.parse') as parse,
              patch('feed2facebook.sqlite3.connect') as connect):
            with self.assertRaisesRegex(RuntimeError, 'fetch login tokens'):
                self.app.main(check_login=True)
        parse.assert_not_called()
        connect.assert_not_called()
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
            {'data': {'story_create': {'post_id': '', 'story_id': '   ', 'story': None}}},
            {'data': {'story_create': {'post_id': True, 'story_id': 123, 'story': None}}},
            {'data': {'story_create': {'logging_token': 'token-only', 'story': None}}},
            {'data': {'story_create': {'post_id': '12345678901234567', 'story': None}},
             'errors': [{'message': 'Request rejected'}]},
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
            self.homepage(), self.post_response(), self.homepage(), self.comment_response(),
            self.homepage(), self.post_response(), self.homepage(), self.comment_response(),
        ]

        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 4)
        self.assertEqual(json.loads(self.posted_form()['variables'])['input']['message']['text'],
                         'Hello & 台灣')

        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1', 'entry-2'])
        self.assertEqual(self.http.call_count, 8)
        self.assertEqual(json.loads(self.posted_form()['variables'])['input']['message']['text'],
                         'Second post')

        self.app.main()
        self.assertEqual(self.http.call_count, 8)

    def test_failure_on_next_run_preserves_successful_entry_and_feed_order(self):
        self.items.insert(0, self.entry(body='<p>Second post</p>', entry_id='entry-2'))
        self.http.side_effect = [
            self.homepage(), self.post_response(), self.homepage(), self.comment_response(),
            self.homepage(), self.response('{"errors":[{"message":"Rejected"}]}'),
        ]

        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 4)

        with self.assertRaises(RuntimeError):
            self.app.main()

        self.assertEqual(self.recorded_ids(), ['entry-1'])
        posts = [call for call in self.http.call_args_list
                 if call.kwargs.get('data', {}).get('fb_api_req_friendly_name')
                 == 'ComposerStoryCreateMutation']
        self.assertEqual(len(posts), 2)
        self.assertEqual(json.loads(posts[0].kwargs['data']['variables'])['input']['message']['text'],
                         'Hello & 台灣')
        self.assertEqual(json.loads(posts[1].kwargs['data']['variables'])['input']['message']['text'],
                         'Second post')

    def test_upstream_handles_json_prefix_and_streamed_responses(self):
        payload = self.post_response().text
        for body in ('for (;;);' + payload, '{}\n' + payload + '\n{"extensions":{}}'):
            with self.subTest(body=body):
                self.http.side_effect = [self.homepage(), self.response(body)]
                self.assertEqual(self.app.post('Hello'), '12345678901234567')

    def test_debug_shows_all_response_frames_and_fbpost_selection_without_values(self):
        first = {'data': {'story_create': None}, 'has_next': True}
        second = json.loads(self.post_response().text)
        second['extensions'] = {
            'token': 'private-token', 'user_id': 123456789,
            'details': [{'text': 'private-response-text', 'complete': True}],
        }
        response = self.response('for (;;);' + json.dumps(first) + '\n' + json.dumps(second))
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        response.headers['Set-Cookie'] = 'xs=private-response-cookie'
        self.http.side_effect = [self.homepage(), response]
        output = io.StringIO()

        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'no post ID or URL'):
                self.app.main(debug=True)

        log = output.getvalue()
        self.assertIn('HTTP 200, content-type="application/json"', log)
        self.assertIn('2 JSON frame(s)', log)
        self.assertIn('response JSON #2', log)
        self.assertIn('"details": [{"text": "<string:', log)
        self.assertIn('fbpost result = {"data": {"story_create": null}, "has_next": true}', log)
        for secret in ('private-token', 'private-session', 'private-csrf', 'private-lsd',
                       'private-response-text', 'private-response-cookie', '123456789', 'story-1'):
            self.assertNotIn(secret, log)
        self.assertEqual(self.recorded_ids(), [])
        self.assertEqual(self.http.call_count, 2)
        self.assertTrue(self.http.call_args.args[0]._closed)

    def test_debug_reports_http_error_without_printing_non_json_content(self):
        self.http.side_effect = [self.homepage(), self.response('<html>private-body</html>', 503)]
        output = io.StringIO()
        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'publish the post'):
                self.app.main(debug=True)
        log = output.getvalue()
        self.assertIn('HTTP 503', log)
        self.assertIn('non-JSON line 1', log)
        self.assertNotIn('private-body', log)
        self.assertEqual(self.recorded_ids(), [])
        self.assertEqual(self.http.call_count, 2)

    def test_debug_reports_missing_response_without_retrying_timeout(self):
        self.http.side_effect = [self.homepage(), RequestsError('Timed out')]
        output = io.StringIO()
        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            with self.assertRaises(RequestsError):
                self.app.main(debug=True)
        self.assertIn('no HTTP response received', output.getvalue())
        self.assertEqual(self.recorded_ids(), [])
        self.assertEqual(self.http.call_count, 2)

    def test_debug_success_still_records_one_entry_and_stops(self):
        self.items.insert(0, self.entry(entry_id='entry-2'))
        output = io.StringIO()
        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            self.app.main(debug=True)
        self.assertIn('1 JSON frame(s)', output.getvalue())
        self.assertIn('fbpost result', output.getvalue())
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 4)

    def test_url_only_confirmation_is_accepted(self):
        self.http.side_effect = [self.homepage(), self.response(json.dumps({
            'data': {'story_create': {'story': {'url': 'https://www.facebook.com/posts/1'}}},
        })), self.homepage(), self.comment_response()]
        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])

    def test_creation_ids_with_null_story_record_success_and_stop_without_duplicates(self):
        # Structure from the reported Facebook response; identifier/token values replaced.
        response = self.response(json.dumps({
            'data': {'story_create': {
                'story_id': 'redacted-story-id',
                'post_id': '12345678901234567',
                'publishing_flow': '<redacted>',
                'story': None,
                'timeline_feed_units_edge': None,
                'logging_token': '<redacted>',
            }},
            'extensions': {
                'server_metadata': {'request_start_time_ms': 1, 'time_at_flush_ms': 2},
                'is_final': True,
            },
        }))
        response.headers['Content-Type'] = 'text/html'
        self.items.insert(0, self.entry(body='<p>Second post</p>', entry_id='entry-2'))
        self.http.side_effect = [
            self.homepage(), response, self.homepage(), self.comment_response(),
            self.homepage(), self.post_response(), self.homepage(), self.comment_response(),
        ]

        with patch('feed2facebook.tprint') as output:
            self.app.main()
        output.assert_any_call('* Facebook post = 12345678901234567')
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 4)

        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1', 'entry-2'])
        self.assertEqual(self.http.call_count, 8)
        self.assertEqual(json.loads(self.posted_form()['variables'])['input']['message']['text'],
                         'Second post')

        self.app.main()
        self.assertEqual(self.http.call_count, 8)

    def test_creation_story_id_is_accepted_without_post_id_or_expanded_story(self):
        self.http.side_effect = [self.homepage(), self.response(json.dumps({
            'data': {'story_create': {'story_id': 'redacted-story-id', 'story': None}},
        }))]
        self.assertEqual(self.app.post('Hello'), 'redacted-story-id')

    def test_body_links_stay_in_main_post_and_source_comment_follows_database_commit(self):
        self.items[:] = [self.entry(body=(
            '<p>Read https://example.org/first and https://example.org/second</p>'
        ))]
        operations = []

        def respond(session, url, **kwargs):
            form = kwargs.get('data')
            if form is None:
                return self.homepage()
            operation = form['fb_api_req_friendly_name']
            operations.append(operation)
            if operation == 'ComposerStoryCreateMutation':
                self.assertEqual(self.recorded_ids(), [])
                return self.post_response()
            self.assertEqual(operation, 'useCometUFICreateCommentMutation')
            self.assertEqual(self.recorded_ids(), ['entry-1'])
            return self.comment_response()

        self.http.side_effect = respond
        self.app.main()

        self.assertEqual(operations, [
            'ComposerStoryCreateMutation', 'useCometUFICreateCommentMutation',
        ])
        self.assertEqual(json.loads(self.posted_form()['variables'])['input']['message']['text'],
                         'Read https://example.org/first and https://example.org/second')
        comment = self.posted_form('useCometUFICreateCommentMutation')
        self.assertEqual(json.loads(comment['variables'])['input']['message']['text'],
                         'Sync from: https://example.com/post')

    def test_empty_body_is_not_posted_or_recorded(self):
        for body in ('', '   ', '<p> </p>', '<p>&nbsp;</p>'):
            with self.subTest(body=body):
                self.items[:] = [self.entry(body=body)]
                self.app.main()
        self.http.assert_not_called()
        self.assertEqual(self.recorded_ids(), [])

    def test_comment_failures_preserve_record_and_are_not_retried(self):
        failures = [
            self.response('private-body', status) for status in (302, 403, 429, 500)
        ] + [
            self.response('private-non-json'), RequestsError('private-request-detail'),
        ] + [self.response(json.dumps(result)) for result in (
            {'errors': [{'message': 'private-rejection'}]},
            {'data': None}, {}, [], {'data': {'comment_create': None}},
            {'data': {'comment_create': {'feedback_comment_edge': {'node': {'id': ''}}}}},
            {'data': {'comment_create': {'comment': {'id': True}}}},
            {'data': {'comment_create': {'comment': {'id': '   '}}}},
            {'data': {'comment_create': {'comment': {'id': 'private-comment'}}},
             'errors': [{'message': 'private-rejection'}]},
        )]
        for failure in failures:
            with self.subTest(failure=failure):
                with closing(sqlite3.connect(self.db_path)) as db:
                    db.execute('DELETE FROM entry')
                    db.commit()
                self.http.reset_mock()
                self.http.side_effect = [
                    self.homepage(), self.post_response(), self.homepage(), failure,
                ]
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output):
                    with self.assertRaisesRegex(RuntimeError, 'main post was recorded') as error:
                        self.app.main()
                self.app.main()
                self.assertEqual(self.recorded_ids(), ['entry-1'])
                self.assertEqual(self.http.call_count, 4)
                self.assertTrue(self.http.call_args.args[0]._closed)
                self.assertEqual(output.getvalue(), '')
                self.assertNotIn('private-', str(error.exception))

    def test_comment_login_failure_keeps_post_recorded_without_claiming_no_post_was_sent(self):
        self.items.insert(0, self.entry(entry_id='entry-2'))
        self.http.side_effect = [
            self.homepage(), self.post_response(), self.response('<html>Log in</html>'),
        ]
        with self.assertRaisesRegex(RuntimeError, 'main post was recorded') as error:
            self.app.main()
        self.assertIn('No source comment was sent', str(error.exception))
        self.assertNotIn('No post was sent', str(error.exception))
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 3)
        self.assertTrue(self.http.call_args.args[0]._closed)

    def test_known_story_token_and_numeric_permalink_identify_the_comment_target(self):
        story_id = base64.b64encode(b'S:_I100001:42:42').decode()
        for created in (
            {'story_id': story_id, 'story': None},
            {'story_id': 'opaque', 'story': {'url': 'https://www.facebook.com/user/posts/42'}},
            {'story': {'url': 'https://www.facebook.com/permalink.php?story_fbid=42&id=100001'}},
        ):
            with self.subTest(created=created):
                with closing(sqlite3.connect(self.db_path)) as db:
                    db.execute('DELETE FROM entry')
                    db.commit()
                self.http.side_effect = [
                    self.homepage(), self.response(json.dumps({'data': {'story_create': created}})),
                    self.homepage(), self.comment_response(),
                ]
                self.app.main()
                form = self.posted_form('useCometUFICreateCommentMutation')
                feedback = json.loads(form['variables'])['input']['feedback_id']
                self.assertEqual(base64.b64decode(feedback).decode(), 'feedback:42')

    def test_unrecognized_post_id_keeps_confirmation_without_guessing_comment_target(self):
        for identifier in ('opaque-story-id', 'https://example.org/posts/42',
                           'https://www.facebook.com/posts/pfbidOpaque',
                           base64.b64encode(b'comment:42_99').decode()):
            with self.subTest(identifier=identifier):
                with closing(sqlite3.connect(self.db_path)) as db:
                    db.execute('DELETE FROM entry')
                    db.commit()
                self.http.reset_mock()
                self.http.side_effect = [self.homepage(), self.response(json.dumps({
                    'data': {'story_create': {'story_id': identifier, 'story': None}},
                }))]
                with self.assertRaisesRegex(RuntimeError, 'No comment was sent'):
                    self.app.main()
                self.app.main()
                self.assertEqual(self.recorded_ids(), ['entry-1'])
                self.assertEqual(self.http.call_count, 2)

    def test_comment_debug_redacts_response_and_accepts_upstream_comment_shape(self):
        result = {'data': {'comment_create': {'comment': {'id': 'comment-1'}}},
                  'extensions': {'token': 'private-token', 'detail': 'private-response-text'}}
        response = self.response('for (;;);' + json.dumps(result))
        response.headers['Set-Cookie'] = 'xs=private-cookie'
        self.http.side_effect = [
            self.homepage(), self.post_response(), self.homepage(), response,
        ]
        output = io.StringIO()
        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            self.app.main(debug=True)
        self.assertIn('source comment response', output.getvalue())
        self.assertIn('Facebook source comment = comment-1', output.getvalue())
        for secret in ('private-token', 'private-response-text', 'private-cookie',
                       'private-session', 'private-csrf', 'private-lsd'):
            self.assertNotIn(secret, output.getvalue())
        self.assertEqual(self.recorded_ids(), ['entry-1'])

    def test_comment_rejection_prints_error_codes_and_schema_hints_without_debug(self):
        response = self.response(json.dumps({
            'errors': [{
                'code': 1675012, 'severity': 'CRITICAL',
                'message': 'A server error noncoercible_variable_value occured. private-csrf',
                'description': "Variable '$translationType' contains private-value",
                'extensions': {
                    'api_error_code': 100, 'error_subcode': 33, 'is_transient': False,
                    'debug_info': 'Missing value for required variable: canUseNicknameOnComet',
                    'token': 'private-token',
                },
            }],
        }))
        response.headers['Content-Type'] = 'application/json'
        response.headers['Set-Cookie'] = 'xs=private-response-cookie'
        self.http.side_effect = [
            self.homepage(), self.post_response(), self.homepage(), response,
        ]
        output = io.StringIO()
        with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'response diagnostics above'):
                self.app.main()
        log = output.getvalue()
        self.assertIn(f'source comment response (doc_id={REPLY_DOC_ID})', log)
        self.assertIn('HTTP 200', log)
        self.assertIn('GraphQL errors #1', log)
        self.assertIn('"code": 1675012', log)
        self.assertIn('"severity": "CRITICAL"', log)
        self.assertIn('"extensions.api_error_code": 100', log)
        self.assertIn('"extensions.error_subcode": 33', log)
        self.assertIn('"extensions.is_transient": false', log)
        self.assertIn('"message_markers": ["noncoercible_variable_value"]', log)
        self.assertIn('"variables": ["canUseNicknameOnComet", "translationType"]', log)
        for secret in ('private-csrf', 'private-value', 'private-token', 'private-response-cookie',
                       'private-session', 'private-lsd'):
            self.assertNotIn(secret, log)
        self.app.main()
        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.http.call_count, 4)

    def test_comment_diagnostics_cover_http_errors_timeouts_and_unconfirmed_results(self):
        for response, diagnostic in (
            (self.response('private-body', 429), 'HTTP 429'),
            (RequestsError('private-timeout'), 'no HTTP response received'),
            (self.response('{"data":{"comment_create":null}}'), '"comment_create": null'),
        ):
            with self.subTest(diagnostic=diagnostic):
                self.http.reset_mock()
                self.http.side_effect = [self.homepage(), response]
                output = io.StringIO()
                with patch('feed2facebook.tprint', side_effect=print), redirect_stdout(output):
                    with self.assertRaises(RuntimeError):
                        self.app.comment('42', 'Sync from: https://example.com/post')
                log = output.getvalue()
                self.assertIn('source comment response', log)
                self.assertIn(diagnostic, log)
                self.assertNotIn('private-', log)
                self.assertEqual(self.http.call_count, 2)

    def test_comment_only_bypasses_main_post_feed_database_and_post_settings(self):
        self.db_path.unlink()
        del self.app._config['default']['feed_url']
        self.app._config['default'].update(
            facebook_privacy='INVALID', facebook_post_doc_id='INVALID',
        )
        self.http.side_effect = [self.homepage(), self.comment_response()]
        with (patch('feed2facebook.feedparser.parse') as parse,
              patch('feed2facebook.sqlite3.connect') as connect,
              patch.object(self.app, 'post') as post):
            self.app.main(comment_only=('42', 'https://example.org/source'))
        parse.assert_not_called()
        connect.assert_not_called()
        post.assert_not_called()
        self.assertFalse(self.db_path.exists())
        self.assertEqual(self.http.call_count, 2)
        form = self.posted_form('useCometUFICreateCommentMutation')
        variables = json.loads(form['variables'])
        self.assertEqual(variables['input']['message']['text'],
                         'Sync from: https://example.org/source')
        self.assertEqual(base64.b64decode(variables['input']['feedback_id']).decode(), 'feedback:42')

    def test_comment_only_failure_does_not_claim_main_post_was_recorded(self):
        self.http.side_effect = [self.homepage(), self.response('{"errors":[{"code":1675012}]}')]
        with self.assertRaisesRegex(RuntimeError, 'No main post was created') as error:
            self.app.main(comment_only=('42', 'https://example.org/source'))
        self.assertNotIn('main post was recorded', str(error.exception))
        self.assertEqual(self.recorded_ids(), [])
        self.assertEqual(self.http.call_count, 2)

    def test_comment_only_rejects_invalid_arguments_before_network_access(self):
        for post_id, source in (
            ('opaque-id', 'https://example.org/source'),
            ('42', '/relative'), ('42', 'file:///private'), ('42', 'https://'),
            ('42', 'https://example.org/\nprivate'), ('42', 'https://[invalid'),
        ):
            with self.subTest(post_id=post_id, source=source):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.app.main(comment_only=(post_id, source))
        self.http.assert_not_called()
        self.assertEqual(self.recorded_ids(), [])

    def test_comment_only_cli_is_mutually_exclusive_with_other_modes(self):
        script = str(Path(__file__).with_name('feed2facebook.py'))
        for option in ('--sync-only', '--check-login'):
            with self.subTest(option=option):
                with (patch('sys.argv', [script, '--comment-only', '42', 'https://example.org', option]),
                      redirect_stderr(io.StringIO())):
                    with self.assertRaises(SystemExit) as error:
                        runpy.run_path(script, run_name='__main__')
                self.assertEqual(error.exception.code, 2)
        self.http.assert_not_called()


if __name__ == '__main__':
    unittest.main()
