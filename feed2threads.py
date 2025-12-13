#!/usr/bin/env python3

import argparse
import configparser
import datetime
import feedparser
import html
import os
import re
import httpx
import sqlite3
import time
import urllib

from lxml.html.clean import Cleaner

class Feed2Threads(object):
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

    def main(self, sync_only=False):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

        if sync_only:
            print('* sync_only mode: will not post to Threads')

        home = os.environ['HOME']
        f_db = '{}/.config/feed2social/feed2threads.sqlite3'.format(home)

        c = self.config
        feed_url = c['default']['feed_url']
        threads_access_token = c['default']['threads_access_token']
        threads_user_id = c['default']['threads_user_id']

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

                # Skip if there is '#nothreads' tag.
                if '#nothreads' in body:
                    continue

                # Remove root's "div".
                body = body.replace('<div>', '').replace('</div>', '')

                # <p> and </p>
                body = body.replace('<p>', '\n').replace('</p>', '\n')

                # trim
                body = body.strip()

                # unescape
                body = html.unescape(body)

                # Limit to 400 chars.
                body = body[0:400]
            else:
                body = ''

            # Generate parameters.
            id_str = item['id']
            url = item['link']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = body
                print('* content = {}'.format(content))

                if sync_only:
                    print('* sync_only: skipping post to Threads')
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                    continue

                # Post to Threads.
                #
                # Step 1: Create media container
                if image_url:
                    # Post with image
                    res = httpx.post('https://graph.threads.net/{}/threads'.format(threads_user_id), data={
                        'media_type': 'IMAGE',
                        'image_url': image_url,
                        'text': content,
                        'access_token': threads_access_token,
                    })
                else:
                    # Post text only
                    res = httpx.post('https://graph.threads.net/{}/threads?text={}&access_token={}&media_type=TEXT'.format(threads_user_id, urllib.parse.quote_plus(content), urllib.parse.quote_plus(threads_access_token)))

                print('* Step 1 - Create container: res = {}'.format(res))
                print('* Step 1 - res.text = {}'.format(res.text))
                if res.status_code != 200:
                    print('* Error creating container, skipping')
                    continue

                creation_id = res.json()['id']

                # Step 1.5: Poll status for image containers
                if image_url:
                    print('* Polling container status for image...')
                    max_attempts = 10
                    poll_interval = 3  # seconds
                    status = 'IN_PROGRESS'

                    for attempt in range(max_attempts):
                        time.sleep(poll_interval)
                        status_res = httpx.get('https://graph.threads.net/v1.0/{}?fields=status&access_token={}'.format(
                            creation_id, urllib.parse.quote_plus(threads_access_token)
                        ))
                        print('* Attempt {}/{}: status_res = {}'.format(attempt + 1, max_attempts, status_res))
                        print('* status_res.text = {}'.format(status_res.text))

                        if status_res.status_code == 200:
                            status = status_res.json().get('status', 'UNKNOWN')
                            print('* Container status: {}'.format(status))
                            if status == 'FINISHED':
                                break
                            elif status == 'ERROR':
                                print('* Container processing failed')
                                break

                    if status != 'FINISHED':
                        print('* Container not ready after {} attempts, skipping'.format(max_attempts))
                        continue

                # Step 2: Publish container
                res = httpx.post('https://graph.threads.net/{}/threads_publish?creation_id={}&access_token={}'.format(threads_user_id, urllib.parse.quote_plus(creation_id), urllib.parse.quote_plus(threads_access_token)))
                print('* Step 2 - Publish: res = {}'.format(res))
                print('* Step 2 - res.text = {}'.format(res.text))

                if res.status_code == 200 and 'id' in res.json():
                    post_id = res.json()['id']
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()

                    # Append feed entry url into replies.
                    #
                    # Step 1: Create reply container
                    res = httpx.post('https://graph.threads.net/v1.0/me/threads', data={
                        'media_type': 'TEXT',
                        'text': f'Sync from: {url}',
                        'reply_to_id': post_id,
                        'access_token': threads_access_token,
                    })
                    print('* Reply Step 1 - Create container: res = {}'.format(res))
                    print('* Reply Step 1 - res.text = {}'.format(res.text))

                    if res.status_code == 200 and 'id' in res.json():
                        # Step 2: Publish reply
                        creation_id = res.json()['id']
                        res = httpx.post('https://graph.threads.net/{}/threads_publish?creation_id={}&access_token={}'.format(threads_user_id, urllib.parse.quote_plus(creation_id), urllib.parse.quote_plus(threads_access_token)))
                        print('* Reply Step 2 - Publish: res = {}'.format(res))
                        print('* Reply Step 2 - res.text = {}'.format(res.text))
                    else:
                        print('* Error creating reply container')
                else:
                    print('* Error publishing container')
                    s.rollback()

if '__main__' == __name__:
    parser = argparse.ArgumentParser(description='Sync feed to Threads')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Threads')
    args = parser.parse_args()

    t = Feed2Threads()
    t.main(sync_only=args.sync_only)
