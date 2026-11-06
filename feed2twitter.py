#!/usr/bin/env python3

import argparse
import asyncio
import configparser
import datetime
import feedparser
import html
import httpx
import os
import sqlite3
import time

from contextlib import AsyncExitStack, closing
from lxml.html.clean import Cleaner
from twikit import Client, InvalidSession, TooManyRequests, TwitterException, Unauthorized

def tprint(*args, **kwargs):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('[%Y-%m-%dT%H:%M:%SZ]')
    print(timestamp, *args, **kwargs)

class Feed2Twitter(object):
    _config = None

    def __init__(self):
        pass

    @property
    def config(self):
        if self._config is None:
            home = os.environ['HOME']
            f_conf = '{}/.config/feed2social/config.ini'.format(home)

            self._config = configparser.ConfigParser()
            self._config.read(f_conf)
        return self._config

    def get_client(self):
        c = self.config['default']
        cookies = {
            'auth_token': c.get('twitter_auth_token', '').strip(),
            'ct0': c.get('twitter_ct0', '').strip(),
        }
        if not all(cookies.values()):
            raise configparser.Error(
                'Set twitter_auth_token and twitter_ct0 in '
                '~/.config/feed2social/config.ini using cookies from x.com.'
            )

        client = Client(
            'en-US',
            impersonate='chrome124',
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ),
        )
        client.set_cookies(cookies)
        return client

    async def upload_media(self, image_url, client):
        """Download an image and upload it using the authenticated client."""
        try:
            tprint('* Downloading image: {}'.format(image_url))
            async with httpx.AsyncClient(timeout=30.0) as downloader:
                img_res = await downloader.get(image_url)
                img_res.raise_for_status()
        except httpx.HTTPError as e:
            tprint('* Failed to download image: {}'.format(e))
            return None

        # Let upload errors stop the run, including expired cookies and rate limits.
        tprint('* Uploading image to Twitter')
        media_id = await client.upload_media(img_res.content)
        tprint('* media_id = {}'.format(media_id))
        return media_id

    async def main(self, sync_only=False):
        tprint('* Started.')

        if sync_only:
            tprint('* sync_only mode: will not post to Twitter')

        home = os.environ['HOME']
        f_db = '{}/.config/feed2social/feed2twitter.sqlite3'.format(home)

        c = self.config
        feed_url = c['default']['feed_url']

        feed = feedparser.parse(feed_url)
        items = feed.entries

        sql_insert = 'INSERT INTO entry (entry_id, created_at) VALUES (?, ?);'
        sql_select = 'SELECT COUNT(*) FROM entry WHERE entry_id = ?;'

        # Workaround: cannot use allow_tags=[]:
        cl = Cleaner(allow_tags=['p'])

        client = None

        async with AsyncExitStack() as stack:
            s = stack.enter_context(closing(sqlite3.connect(f_db)))
            for item in reversed(items):
                body = item['description']

                # Print out item's id.
                tprint('* item.id = {}'.format(item.id))

                # Check if entry has media content (images)
                image_url = None
                if hasattr(item, 'media_content'):
                    for media in item.media_content:
                        if media.get('type', '').startswith('image/'):
                            image_url = media.get('url')
                            break

                # Skip if body is empty and no image.
                if (not body or not body.strip()) and not image_url:
                    tprint('* Skipping: empty body and no image')
                    continue

                # Craft "body".
                #
                # Remove all tags except paragraphs and the root div.
                if body and body.strip():
                    body = cl.clean_html(body)

                    # Skip if there is '#notwitter' tag.
                    if '#notwitter' in body:
                        continue

                    # Remove root's "div".
                    body = body.replace('<div>', '').replace('</div>', '')

                    # <p> and </p>
                    body = body.replace('<p>', '\n').replace('</p>', '\n')

                    # trim
                    body = body.strip()

                    # unescape
                    body = html.unescape(body)

                    # Limit to 280 chars.
                    body = body[0:280]
                else:
                    body = ''

                # Generate parameters.
                id_str = item['id']
                url = item['link']

                cur = s.cursor()

                cur.execute(sql_select, (id_str, ))
                if 0 == cur.fetchone()[0]:
                    content = body
                    tprint('* content = {}'.format(content))

                    if sync_only:
                        tprint('* sync_only: skipping post to Twitter')
                        cur.execute(sql_insert, (id_str, int(time.time())))
                        s.commit()
                        continue

                    if client is None:
                        client = self.get_client()
                        # twifork 2.4 has no public close method for its two transports.
                        stack.push_async_callback(client.http.aclose)
                        stack.push_async_callback(client._curl_session.close)

                    # Upload media if present
                    media_id = None
                    if image_url:
                        media_id = await self.upload_media(image_url, client)
                        if media_id:
                            # Wait after media upload to avoid rate limit
                            tprint('* Waiting 2 seconds after media upload...')
                            await asyncio.sleep(2)

                    if not content and not media_id:
                        tprint('* Skipping: no text or uploaded image')
                        continue

                    # Post to Twitter.
                    tweet = await client.create_tweet(
                        text=content,
                        media_ids=[media_id] if media_id else None,
                    )
                    tprint('* tweet.id = {}'.format(tweet.id))

                    # Record the main tweet before replying to avoid reposting it on failure.
                    cur.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()

                    # Wait before posting reply to avoid rate limit
                    tprint('* Waiting 2 seconds before posting reply...')
                    await asyncio.sleep(2)

                    # Append feed entry url into replies.
                    reply = await client.create_tweet(
                        text=f'Sync from: {url}',
                        reply_to=tweet.id,
                    )
                    tprint('* reply.id = {}'.format(reply.id))

                    # Wait between processing feed items to avoid rate limit
                    tprint('* Waiting 3 seconds before next item...')
                    await asyncio.sleep(3)

if '__main__' == __name__:
    parser = argparse.ArgumentParser(description='Sync feed to Twitter')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Twitter')
    args = parser.parse_args()

    t = Feed2Twitter()
    try:
        asyncio.run(t.main(sync_only=args.sync_only))
    except TooManyRequests as e:
        tprint('* Rate limit exceeded. Exiting.')
        headers = e.headers or {}
        for name in ('x-rate-limit-limit', 'x-rate-limit-remaining', 'x-rate-limit-reset'):
            tprint('*   {}: {}'.format(name, headers.get(name, 'N/A')))
        raise SystemExit(1)
    except (Unauthorized, InvalidSession):
        tprint('* Twitter session expired or rejected. Update twitter_auth_token and twitter_ct0.')
        raise SystemExit(1)
    except (TwitterException, configparser.Error) as e:
        tprint('* {}: {}. Exiting.'.format(type(e).__name__, e))
        raise SystemExit(1)
