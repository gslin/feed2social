#!/usr/bin/env python3

import configparser
import datetime
import feedparser
import html
import os
import re
import requests
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

    def main(self):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

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

            # Craft "body".
            #
            # First to remove all tags except "a" and root's "div".
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

            # Generate parameters.
            id_str = item['id']
            url = item['id']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = '{}\n\n{}'.format(body, url)
                print('* content = {}'.format(content))

                res = requests.post('https://graph.threads.net/{}/threads?text={}&access_token={}&media_type=TEXT'.format(threads_user_id, urllib.parse.quote_plus(content), urllib.parse.quote_plus(threads_access_token)))
                print('* res = {}'.format(res))

                creation_id = res.json()['id']
                res = requests.post('https://graph.threads.net/{}/threads_publish?creation_id={}&access_token={}'.format(threads_user_id, urllib.parse.quote_plus(creation_id), urllib.parse.quote_plus(threads_access_token)))
                print('* res = {}'.format(res))

                if res.json()['id']:
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                else:
                    s.rollback()

if '__main__' == __name__:
    t = Feed2Threads()
    t.main()
