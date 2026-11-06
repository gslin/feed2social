"""Offline checks for cookie authentication and feed posting."""

import configparser
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, create_autospec, patch

import feedparser
import httpx
from curl_cffi.requests import AsyncSession
from twikit import Client, CouldNotTweet, InvalidSession, TooManyRequests, Unauthorized

from feed2twitter import Feed2Twitter


class Feed2TwitterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / 'entries.sqlite3'
        self.connect = sqlite3.connect
        with closing(self.connect(self.db_path)) as db:
            db.execute('CREATE TABLE entry (entry_id VARCHAR, created_at INT)')

        self.enterContext(patch(
            'feed2twitter.sqlite3.connect',
            side_effect=lambda path: self.connect(self.db_path),
        ))
        self.app = Feed2Twitter()
        self.app._config = configparser.ConfigParser()
        self.app._config.read_dict({'default': {'feed_url': 'https://example.com/feed'}})
        self.items = [self.entry()]
        self.enterContext(patch(
            'feed2twitter.feedparser.parse',
            return_value=SimpleNamespace(entries=self.items),
        ))
        self.client = create_autospec(Client, instance=True)
        self.client.http = SimpleNamespace(aclose=AsyncMock())
        self.client._curl_session = SimpleNamespace(close=AsyncMock())
        self.client.upload_media.return_value = 'media-1'
        self.client.create_tweet.side_effect = [
            SimpleNamespace(id='tweet-1'), SimpleNamespace(id='reply-1'),
        ]
        self.get_client = self.enterContext(patch.object(
            self.app, 'get_client', return_value=self.client,
        ))
        self.enterContext(patch('feed2twitter.asyncio.sleep', new_callable=AsyncMock))
        self.enterContext(patch('feed2twitter.tprint'))
        self.enterContext(patch(
            'socket.create_connection', side_effect=AssertionError('Unexpected network access'),
        ))

    @staticmethod
    def entry(body='<p>Hello &amp; world</p>', image=False, entry_id='entry-1'):
        item = feedparser.FeedParserDict(
            id=entry_id, description=body, link='https://example.com/post',
        )
        if image:
            item.media_content = [{'type': 'image/png', 'url': 'https://example.com/image.png'}]
        return item

    def recorded_ids(self):
        with closing(self.connect(self.db_path)) as db:
            return [row[0] for row in db.execute('SELECT entry_id FROM entry ORDER BY entry_id')]

    def mock_image_download(self, status=200):
        downloader = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, content=b'image bytes'),
        ))
        self.enterContext(patch('feed2twitter.httpx.AsyncClient', return_value=downloader))

    async def test_posts_source_reply_and_does_not_repeat_processed_entries(self):
        await self.app.main()
        await self.app.main()

        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.client.create_tweet.await_args_list, [
            call(text='Hello & world', media_ids=None),
            call(text='Sync from: https://example.com/post', reply_to='tweet-1'),
        ])
        self.get_client.assert_called_once()
        self.client.http.aclose.assert_awaited_once()
        self.client._curl_session.close.assert_awaited_once()

    async def test_posts_downloaded_image_with_text(self):
        self.items[:] = [self.entry(image=True)]
        self.mock_image_download()

        await self.app.main()

        self.client.upload_media.assert_awaited_once_with(b'image bytes')
        self.assertEqual(self.client.create_tweet.await_args_list[0],
                         call(text='Hello & world', media_ids=['media-1']))
        self.assertEqual(self.recorded_ids(), ['entry-1'])

    async def test_posts_image_without_text(self):
        self.items[:] = [self.entry(body='', image=True)]
        self.mock_image_download()

        await self.app.main()

        self.assertEqual(self.client.create_tweet.await_args_list[0],
                         call(text='', media_ids=['media-1']))
        self.assertEqual(self.recorded_ids(), ['entry-1'])

    async def test_sync_only_records_entries_without_cookies_or_client(self):
        self.items[:] = [self.entry(image=True)]

        await self.app.main(sync_only=True)

        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.get_client.assert_not_called()
        self.client.upload_media.assert_not_awaited()
        self.client.create_tweet.assert_not_awaited()

    async def test_skips_excluded_and_empty_entries_without_client(self):
        self.items[:] = [self.entry(body='#notwitter'), self.entry(body='')]

        await self.app.main()

        self.assertEqual(self.recorded_ids(), [])
        self.get_client.assert_not_called()

    async def test_failed_main_post_stops_without_recording_or_posting_later_entries(self):
        self.items.insert(0, self.entry(entry_id='entry-2'))
        for error in (Unauthorized, InvalidSession, TooManyRequests, CouldNotTweet):
            with self.subTest(error=error.__name__):
                self.client.reset_mock()
                self.client.create_tweet.side_effect = error('Posting failed')
                with self.assertRaises(error):
                    await self.app.main()
                self.assertEqual(self.recorded_ids(), [])
                self.client.create_tweet.assert_awaited_once()

    async def test_upload_rate_limit_stops_before_posting_or_recording(self):
        self.items[:] = [self.entry(image=True)]
        self.mock_image_download()
        self.client.upload_media.side_effect = TooManyRequests('Upload rate limit')

        with self.assertRaises(TooManyRequests):
            await self.app.main()

        self.assertEqual(self.recorded_ids(), [])
        self.client.create_tweet.assert_not_awaited()
        self.client.http.aclose.assert_awaited_once()
        self.client._curl_session.close.assert_awaited_once()

    async def test_reply_failure_keeps_main_post_recorded(self):
        self.client.create_tweet.side_effect = [
            SimpleNamespace(id='tweet-1'), TooManyRequests('Reply rate limit'),
        ]

        with self.assertRaises(TooManyRequests):
            await self.app.main()
        await self.app.main()

        self.assertEqual(self.recorded_ids(), ['entry-1'])
        self.assertEqual(self.client.create_tweet.await_count, 2)

    async def test_image_download_failure_can_still_post_text(self):
        self.items[:] = [self.entry(image=True)]
        self.mock_image_download(status=404)

        await self.app.main()

        self.client.upload_media.assert_not_awaited()
        self.assertEqual(self.client.create_tweet.await_args_list[0],
                         call(text='Hello & world', media_ids=None))
        self.assertEqual(self.recorded_ids(), ['entry-1'])

    async def test_image_only_download_failure_is_not_recorded(self):
        self.items[:] = [self.entry(body='', image=True)]
        self.mock_image_download(status=404)

        await self.app.main()

        self.client.create_tweet.assert_not_awaited()
        self.assertEqual(self.recorded_ids(), [])

    async def test_real_client_loads_cookies_and_impersonation_backend(self):
        self.app._config['default'].update(
            twitter_auth_token='test-auth-token', twitter_ct0='test-csrf-token',
        )
        client = Feed2Twitter.get_client(self.app)
        try:
            self.assertIsInstance(client._curl_session, AsyncSession)
            self.assertEqual(client._impersonate, 'chrome124')
            self.assertEqual(client.get_cookies()['auth_token'], 'test-auth-token')
            self.assertEqual(client.get_cookies()['ct0'], 'test-csrf-token')
        finally:
            await client._curl_session.close()
            await client.http.aclose()

    async def test_missing_cookies_report_configuration_error(self):
        with self.assertRaisesRegex(configparser.Error, 'twitter_auth_token and twitter_ct0'):
            Feed2Twitter.get_client(self.app)


if __name__ == '__main__':
    unittest.main()
