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
    def __init__(self):
        pass

    def main(self):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

        home = os.environ['HOME']
        f_conf = '{}/.config/feed2social/config.ini'.format(home)
        f_db = '{}/.config/feed2social/feed2plurk.sqlite3'.format(home)

        c = configparser.ConfigParser()
        c.read(f_conf)

        feed_url = c['default']['feed_url']
        feed = feedparser.parse(feed_url)
        items = feed.entries

        p_ak = c['default']['plurk_app_key']
        p_as = c['default']['plurk_app_secret']
        p_tk = c['default']['plurk_token']
        p_ts = c['default']['plurk_token_secret']
        p = plurk_oauth.PlurkAPI(p_ak, p_as)
        p.authorize(p_tk, p_ts)

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

            # unescape
            text = html.unescape(text)

            # Limit to 200 chars.
            text = text[0:200]

            # Generate parameters.
            id_str = item['id']
            url = item['id']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = '{}\n\n{}'.format(text, url)
                print('* content = {}'.format(content))

                res = p.callAPI('/APP/Timeline/plurkAdd', {
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

if '__main__' == __name__:
    t = Feed2Plurk()
    t.main()
