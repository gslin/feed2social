#!/usr/bin/env python3

import argparse
import configparser
import datetime
import feedparser
import html
import httpx
import os
import plurk_oauth
import re
import sqlite3
import tempfile
import time

from lxml.html.clean import Cleaner

class Feed2Plurk(object):
    _client = None
    _config = None

    def __init__(self):
        pass

    @property
    def client(self):
        if self._client is None:
            p_ak = self.config['default']['plurk_app_key']
            p_as = self.config['default']['plurk_app_secret']
            p_tk = self.config['default']['plurk_token']
            p_ts = self.config['default']['plurk_token_secret']
            self._client = plurk_oauth.PlurkAPI(p_ak, p_as)
            self._client.authorize(p_tk, p_ts)
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
            print('* sync_only mode: will not post to Plurk')

        home = os.environ['HOME']
        f_db = '{}/.config/feed2social/feed2plurk.sqlite3'.format(home)

        feed_url = self.config['default']['feed_url']
        feed = feedparser.parse(feed_url)
        items = feed.entries

        s = sqlite3.connect(f_db)

        sql_insert = 'INSERT INTO entry (entry_id, created_at) VALUES (?, ?);'
        sql_select = 'SELECT COUNT(*) FROM entry WHERE entry_id = ?;'

        # Workaround: cannot use allow_tags=[]:
        cl = Cleaner(allow_tags=['p'])

        for item in reversed(items):
            text = item['description']

            # Print out item's id.
            print('* item.id = {}'.format(item.id))

            # Check if entry has media content (images)
            image_url = None
            if hasattr(item, 'media_content'):
                for media in item.media_content:
                    if media.get('type', '').startswith('image/'):
                        image_url = media.get('url')
                        break

            # Skip if text is empty and no image.
            if (not text or not text.strip()) and not image_url:
                print('* Skipping: empty body and no image')
                continue

            # Craft "text".
            #
            # First to remove all tags except "a" and root's "div".
            if text and text.strip():
                text = cl.clean_html(text)

                # Skip if there is '#noplurk' tag.
                if '#noplurk' in text:
                    continue

                # Remove root's "div".
                text = text.replace('<div>', '').replace('</div>', '')

                # <p> and </p>
                text = text.replace('<p>', '\n').replace('</p>', '\n')

                # trim
                text = text.strip()

                # unescape
                text = html.unescape(text)

                # Limit to 360 unicode chars.
                text = text[:360]
            else:
                text = ''

            # Generate parameters.
            id_str = item['id']
            url = item['link']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = text
                print('* content = {}'.format(content))

                if sync_only:
                    print('* sync_only: skipping post to Plurk')
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                    continue

                # Download and upload image if present
                if image_url:
                    try:
                        print('* Downloading image: {}'.format(image_url))
                        img_res = httpx.get(image_url, timeout=30.0)
                        if img_res.status_code == 200:
                            image_data = img_res.content
                            print('* Image downloaded: {} bytes'.format(len(image_data)))

                            # Save image to temp file and upload to Plurk
                            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                                tmp_file.write(image_data)
                                tmp_path = tmp_file.name

                            try:
                                print('* Uploading image to Plurk...')
                                upload_res = self.client.callAPI('/APP/Timeline/uploadPicture', {}, fpath=tmp_path)
                                print('* type(upload_res) = {}'.format(type(upload_res)))
                                print('* upload_res = {}'.format(upload_res))

                                if isinstance(upload_res, dict) and 'full' in upload_res:
                                    plurk_image_url = upload_res['full']
                                    print('* Plurk image URL: {}'.format(plurk_image_url))
                                    # Append image URL to content
                                    if content:
                                        content = content + '\n' + plurk_image_url
                                    else:
                                        content = plurk_image_url
                                else:
                                    print('* Failed to upload image to Plurk')
                            finally:
                                # Clean up temp file
                                os.unlink(tmp_path)
                        else:
                            print('* Failed to download image: {}'.format(img_res.status_code))
                    except Exception as e:
                        print('* Exception handling image: {}'.format(e))

                res = self.client.callAPI('/APP/Timeline/plurkAdd', {
                    'content': content,
                    'qualifier': ':',
                })

                print('* type(item) = {}'.format(type(item)))
                print('* item = {}'.format(item))
                print('* type(res) = {}'.format(type(res)))
                print('* res = {}'.format(res))
                if isinstance(res, dict) and res['plurk_id'] > 0:
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                else:
                    s.rollback()

                # Append feed entry url into comments.
                plurk_id = res['plurk_id']
                res = self.client.callAPI('/APP/Responses/responseAdd', {
                    'content': f'Sync from: {url}',
                    'plurk_id': plurk_id,
                    'qualifier': ':',
                })
                print('* type(res) = {}'.format(type(res)))
                print('* res = {}'.format(res))

if '__main__' == __name__:
    parser = argparse.ArgumentParser(description='Sync feed to Plurk')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Plurk')
    args = parser.parse_args()

    t = Feed2Plurk()
    t.main(sync_only=args.sync_only)
