#!/usr/bin/env python3

import atproto
import configparser
import datetime
import feedparser
import html
import os
import re
import sqlite3
import time

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
            self._client = atproto.Client()
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

    def main(self):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

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
            url = item['id']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = '{}\n\n'.format(body)
                print('* content = {}'.format(content))

                tb = atproto.client_utils.TextBuilder()
                tb.text(content)
                tb.link(url, url)
                post = self.client.send_post(tb)

                print('* type(post) = {}'.format(type(post)))
                print('* post = {}'.format(post))
                if isinstance(post, object) and post.cid:
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                else:
                    s.rollback()

if '__main__' == __name__:
    t = Feed2Bluesky()
    t.main()
