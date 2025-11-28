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
        )

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

            # Craft "body".
            #
            # First to remove all tags except "a" and root's "div".
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

                # Post to Twitter.
                res = httpx.post(
                    'https://api.x.com/2/tweets',
                    auth=auth,
                    json={'text': content},
                )
                print('* res = {}'.format(res))
                print('* res.text = {}'.format(res.text))
                if res.status_code != 201:
                    continue

                tweet_id = res.json()['data']['id']

                cur.execute(sql_insert, (id_str, int(time.time())))
                s.commit()

                # Append feed entry url into replies.
                res = httpx.post(
                    'https://api.x.com/2/tweets',
                    auth=auth,
                    json={
                        'text': f'Sync from: {url}',
                        'reply': {'in_reply_to_tweet_id': tweet_id},
                    },
                )
                print('* res = {}'.format(res))
                print('* res.text = {}'.format(res.text))

if '__main__' == __name__:
    parser = argparse.ArgumentParser(description='Sync feed to Twitter')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Twitter')
    args = parser.parse_args()

    t = Feed2Twitter()
    t.main(sync_only=args.sync_only)
