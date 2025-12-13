#!/usr/bin/env python3

import argparse
import configparser
import datetime
import feedparser
import html
import httpx
import io
import os
import re
import sqlite3
import time

from authlib.integrations.httpx_client import OAuth1Auth
from lxml.html.clean import Cleaner

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

    def get_auth(self):
        c = self.config
        return OAuth1Auth(
            client_id=c['default']['twitter_api_key'],
            client_secret=c['default']['twitter_api_key_secret'],
            token=c['default']['twitter_access_token'],
            token_secret=c['default']['twitter_access_token_secret'],
            force_include_body=True,  # keep JSON payload so Twitter sees the text
        )

    def upload_media(self, image_url, auth):
        """Download image from URL and upload to Twitter v1.1 API"""
        try:
            # Download image
            print('* Downloading image: {}'.format(image_url))
            img_res = httpx.get(image_url, timeout=30.0)
            if img_res.status_code != 200:
                print('* Failed to download image: {}'.format(img_res.status_code))
                return None

            # Upload to Twitter v1.1 API
            print('* Uploading image to Twitter v1.1 API')
            upload_res = httpx.post(
                'https://upload.twitter.com/1.1/media/upload.json',
                auth=auth,
                files={'media': io.BytesIO(img_res.content)},
            )
            print('* upload_res = {}'.format(upload_res))
            print('* upload_res.text = {}'.format(upload_res.text))

            if upload_res.status_code == 200:
                media_id = upload_res.json()['media_id_string']
                print('* media_id = {}'.format(media_id))
                return media_id
            else:
                print('* Failed to upload image')
                return None
        except Exception as e:
            print('* Exception during media upload: {}'.format(e))
            return None

    def main(self, sync_only=False):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

        if sync_only:
            print('* sync_only mode: will not post to Twitter')

        home = os.environ['HOME']
        f_db = '{}/.config/feed2social/feed2twitter.sqlite3'.format(home)

        c = self.config
        feed_url = c['default']['feed_url']

        feed = feedparser.parse(feed_url)
        items = feed.entries

        s = sqlite3.connect(f_db)

        sql_insert = 'INSERT INTO entry (entry_id, created_at) VALUES (?, ?);'
        sql_select = 'SELECT COUNT(*) FROM entry WHERE entry_id = ?;'

        # Workaround: cannot use allow_tags=[]:
        cl = Cleaner(allow_tags=['p'])

        auth = self.get_auth()

        for item in reversed(items):
            body = item['description']

            # Print out item's id.
            print('* item.id = {}'.format(item.id))

            # Check if entry has media content (images)
            image_url = None
            if hasattr(item, 'media_content'):
                for media in item.media_content:
                    if media.get('type', '').startswith('image/'):
                        image_url = media.get('url')
                        break

            # Skip if body is empty and no image.
            if (not body or not body.strip()) and not image_url:
                print('* Skipping: empty body and no image')
                continue

            # Craft "body".
            #
            # First to remove all tags except "a" and root's "div".
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
                print('* content = {}'.format(content))

                if sync_only:
                    print('* sync_only: skipping post to Twitter')
                    cur.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                    continue

                # Upload media if present
                media_id = None
                if image_url:
                    media_id = self.upload_media(image_url, auth)
                    if media_id:
                        # Wait after media upload to avoid rate limit
                        print('* Waiting 2 seconds after media upload...')
                        time.sleep(2)

                # Post to Twitter.
                tweet_data = {'text': content}
                if media_id:
                    tweet_data['media'] = {'media_ids': [media_id]}

                res = httpx.post(
                    'https://api.x.com/2/tweets',
                    auth=auth,
                    json=tweet_data,
                )
                print('* res = {}'.format(res))
                print('* res.text = {}'.format(res.text))

                if res.status_code == 429:
                    # Rate limit hit, display headers and exit
                    print('* Rate limit exceeded (429). Response headers:')
                    print('*   x-rate-limit-limit: {}'.format(res.headers.get('x-rate-limit-limit', 'N/A')))
                    print('*   x-rate-limit-remaining: {}'.format(res.headers.get('x-rate-limit-remaining', 'N/A')))
                    print('*   x-rate-limit-reset: {}'.format(res.headers.get('x-rate-limit-reset', 'N/A')))
                    rate_limit_reset = res.headers.get('x-rate-limit-reset')
                    if rate_limit_reset:
                        reset_time = int(rate_limit_reset)
                        reset_datetime = datetime.datetime.fromtimestamp(reset_time)
                        print('*   Reset time: {} (local time)'.format(reset_datetime))
                    print('* Exiting due to rate limit.')
                    s.close()
                    exit(1)

                if res.status_code != 201:
                    print('* Error posting tweet: {}'.format(res.status_code))
                    continue

                tweet_id = res.json()['data']['id']

                cur.execute(sql_insert, (id_str, int(time.time())))
                s.commit()

                # Wait before posting reply to avoid rate limit
                print('* Waiting 2 seconds before posting reply...')
                time.sleep(2)

                # Append feed entry url into replies.
                reply_data = {
                    'text': f'Sync from: {url}',
                    'reply': {'in_reply_to_tweet_id': tweet_id},
                }

                res = httpx.post(
                    'https://api.x.com/2/tweets',
                    auth=auth,
                    json=reply_data,
                )
                print('* Reply res = {}'.format(res))
                print('* Reply res.text = {}'.format(res.text))

                if res.status_code == 429:
                    # Rate limit hit, display headers and exit
                    print('* Reply rate limit exceeded (429). Response headers:')
                    print('*   x-rate-limit-limit: {}'.format(res.headers.get('x-rate-limit-limit', 'N/A')))
                    print('*   x-rate-limit-remaining: {}'.format(res.headers.get('x-rate-limit-remaining', 'N/A')))
                    print('*   x-rate-limit-reset: {}'.format(res.headers.get('x-rate-limit-reset', 'N/A')))
                    rate_limit_reset = res.headers.get('x-rate-limit-reset')
                    if rate_limit_reset:
                        reset_time = int(rate_limit_reset)
                        reset_datetime = datetime.datetime.fromtimestamp(reset_time)
                        print('*   Reset time: {} (local time)'.format(reset_datetime))
                    print('* Exiting due to rate limit.')
                    s.close()
                    exit(1)

                if res.status_code != 201:
                    print('* Error posting reply: {}'.format(res.status_code))

                # Wait between processing feed items to avoid rate limit
                print('* Waiting 3 seconds before next item...')
                time.sleep(3)

if '__main__' == __name__:
    parser = argparse.ArgumentParser(description='Sync feed to Twitter')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Twitter')
    args = parser.parse_args()

    t = Feed2Twitter()
    t.main(sync_only=args.sync_only)
