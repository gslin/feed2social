#!/usr/bin/env python3

import configparser
import datetime
import feedparser
import html
import os
import plurk_oauth
import re
import sqlite3
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

    def main(self):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

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

            # Craft "text".
            #
            # First to remove all tags except "a" and root's "div".
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

            # Generate parameters.
            id_str = item['id']
            url = item['link']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = text
                print('* content = {}'.format(content))

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
                    'content': url,
                    'plurk_id': plurk_id,
                    'qualifier': ':',
                })
                print('* type(res) = {}'.format(type(res)))
                print('* res = {}'.format(res))

if '__main__' == __name__:
    t = Feed2Plurk()
    t.main()
