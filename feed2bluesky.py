#!/usr/bin/env python3

import argparse
import configparser
import datetime
import feedparser
import html
import httpx
import os
import re
import sqlite3
import time

from atproto import Client, client_utils, models
from lxml.html.clean import Cleaner

class Feed2Bluesky(object):
    _client = None
    _config = None

    def __init__(self):
        pass

    @property
    def client(self):
        if self._client is None:
            bsky_username = self.config['default']['bluesky_username']
            bsky_password = self.config['default']['bluesky_password']
            self._client = Client()
            profile = self._client.login(bsky_username, bsky_password)
        return self._client

    @property
    def config(self):
        if self._config is None:
            home = os.environ['HOME']
            f_conf = '{}/.config/feed2social/config.ini'.format(home)

            self._config = configparser.ConfigParser()
            self._config.read(f_conf)
        return self._config

    def main(self, sync_only=False):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

        if sync_only:
            print('* sync_only mode: will not post to Bluesky')

        home = os.environ['HOME']
        f_db = '{}/.config/feed2social/feed2bluesky.sqlite3'.format(home)

        feed_url = self.config['default']['feed_url']
        feed = feedparser.parse(feed_url)
        items = feed.entries

        s = sqlite3.connect(f_db)

        sql_insert = 'INSERT INTO entry (entry_id, created_at) VALUES (?, ?);'
        sql_select = 'SELECT COUNT(*) FROM entry WHERE entry_id = ?;'

        # Workaround: cannot use allow_tags=[]:
        cl = Cleaner(allow_tags=['p'])

        for item in reversed(items):
            body = item['description']

            # Print out item's id.
            print('* item.id = {}'.format(item.id))

            # Skip if body is empty.
            if not body or not body.strip():
                print('* Skipping: empty body')
                continue

            # Craft "body".
            #
            # First to remove all tags except "a" and root's "div".
            body = cl.clean_html(body)

            # Skip if there is '#nobluesky' tag.
            if '#nobluesky' in body:
                continue

            # Remove root's "div".
            body = body.replace('<div>', '').replace('</div>', '')

            # <p> and </p>
            body = body.replace('<p>', '\n').replace('</p>', '\n')

            # trim
            body = body.strip()

            # unescape
            body = html.unescape(body)

            # Limit to 200 chars.
            body = body[0:200]

            # Generate parameters.
            id_str = item['id']
            url = item['link']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = body
                print('* content = {}'.format(content))

                if sync_only:
                    print('* sync_only: skipping post to Bluesky')
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                    continue

                # Check if entry has media content (images)
                image_url = None
                image_data = None
                if hasattr(item, 'media_content'):
                    for media in item.media_content:
                        # Check if it's an image
                        if media.get('type', '').startswith('image/'):
                            image_url = media.get('url')
                            print('* Found image: {}'.format(image_url))
                            break

                # Download image if present
                if image_url:
                    try:
                        print('* Downloading image: {}'.format(image_url))
                        img_res = httpx.get(image_url, timeout=30.0)
                        if img_res.status_code == 200:
                            image_data = img_res.content
                            print('* Image downloaded: {} bytes'.format(len(image_data)))
                        else:
                            print('* Failed to download image: {}'.format(img_res.status_code))
                    except Exception as e:
                        print('* Exception downloading image: {}'.format(e))

                # Post to Bluesky
                if image_data:
                    # Post with image using send_image
                    tb = client_utils.TextBuilder()

                    # Handle links
                    http_pattern = re.compile(r'^https?://[^\s]+')
                    for w in re.split(r'(https?://[^\s]+)', content):
                        if len(w) == 0:
                            continue

                        if http_pattern.match(w):
                            tb.link(w, w)
                        else:
                            tb.text(w)

                    post = self.client.send_image(text=tb, image=image_data, image_alt='')
                else:
                    # Post text only
                    tb = client_utils.TextBuilder()

                    # Handle links
                    http_pattern = re.compile(r'^https?://[^\s]+')
                    for w in re.split(r'(https?://[^\s]+)', content):
                        if len(w) == 0:
                            continue

                        if http_pattern.match(w):
                            tb.link(w, w)
                        else:
                            tb.text(w)

                    post = self.client.send_post(tb)

                print('* type(post) = {}'.format(type(post)))
                print('* post = {}'.format(post))
                if isinstance(post, object) and post.cid:
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                else:
                    s.rollback()

                tb2 = client_utils.TextBuilder()
                tb2.text('Sync from: ')
                tb2.link(url, url)

                post_ref = models.create_strong_ref(post)
                reply = self.client.send_post(tb2, reply_to=models.AppBskyFeedPost.ReplyRef(parent=post_ref, root=post_ref))
                print('* type(reply) = {}'.format(type(reply)))
                print('* reply = {}'.format(reply))

if '__main__' == __name__:
    parser = argparse.ArgumentParser(description='Sync feed to Bluesky')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Bluesky')
    args = parser.parse_args()

    t = Feed2Bluesky()
    t.main(sync_only=args.sync_only)
