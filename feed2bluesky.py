#!/usr/bin/env python3

import atproto
import configparser
import feedparser
import html
import os
import re
import sqlite3
import time

from lxml.html.clean import Cleaner

class Feed2Bluesky(object):
    def __init__(self):
        pass

    def start(self):
        home = os.environ['HOME']
        f_conf = '{}/.config/feed2social/config.ini'.format(home)
        f_db = '{}/.config/feed2social/feed2bluesky.sqlite3'.format(home)

        c = configparser.ConfigParser()
        c.read(f_conf)

        feed_url = c['default']['feed_url']
        feed = feedparser.parse(feed_url)
        items = feed.entries

        bsky_username = c['default']['bluesky_username']
        bsky_password = c['default']['bluesky_password']
        client = atproto.Client()
        profile = client.login(bsky_username, bsky_password)

        s = sqlite3.connect(f_db)

        sql_insert = 'INSERT INTO entry (entry_id, created_at) VALUES (?, ?);'
        sql_select = 'SELECT COUNT(*) FROM entry WHERE entry_id = ?;'

        # Workaround: cannot use allow_tags=[]:
        cl = Cleaner(allow_tags=['p'])

        for item in reversed(items):
            text = item['description']

            # Print out details.
            print('* item = {}'.format(item))

            # Craft "text".
            #
            # First to remove all tags except "a" and root's "div".
            text = cl.clean_html(text)

            # Skip if there is '#nobluesky' tag.
            if '#nobluesky' in text:
                continue

            # Remove root's "div".
            text = text.replace('<div>', '').replace('</div>', '')

            # <p> and </p>
            text = text.replace('<p>', '\n').replace('</p>', '\n')

            # unescape
            text = html.unescape(text)

            # Generate parameters.
            id_str = item['id']
            url = item['id']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = '{}\n\n{}'.format(text, url)
                print('* content = {}'.format(content))

                text = atproto.client_utils.TextBuilder().text(text)
                post = client.send_post(text)

                print('* type(item) = {}'.format(type(item)))
                print('* item = {}'.format(item))
                print('* type(res) = {}'.format(type(res)))
                print('* res = {}'.format(res))
                if type(res) is dict and res['plurk_id'] > 0:
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                else:
                    s.rollback()

if '__main__' == __name__:
    t = Feed2Bluesky()
    t.start()
