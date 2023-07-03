#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import feedparser
import html
import os
import re
import requests
import selenium
import selenium.webdriver.firefox.options
import sentry_sdk
import sqlite3
import time

from lxml.html.clean import Cleaner
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.firefox_binary import FirefoxBinary

class Feed2Facebook(object):
    b = None

    def init_browser(self):
        if self.b is not None:
            return

        home = os.environ['HOME']

        firefox_binary = '/usr/bin/firefox-esr'
        profile_dir = home + '/.mozilla/firefox-esr/selenium'

        options = selenium.webdriver.firefox.options.Options()
        options.headless = True
        options.profile = profile_dir

        self.b = selenium.webdriver.Firefox(firefox_binary=FirefoxBinary(firefox_binary), options=options)

    def post(self, text):
        self.init_browser()

        b = self.b
        url = 'https://mbasic.facebook.com/'

        b.get(url)

        t = b.find_element(by=By.CSS_SELECTOR, value='#mbasic_inline_feed_composer textarea')
        t.send_keys(text)

        btn = b.find_element(by=By.CSS_SELECTOR, value='#mbasic_inline_feed_composer input[value="Post"]')
        btn.click()

    def main(self):
        home = os.environ['HOME']
        f_conf = '{}/.config/feed2social/config.ini'.format(home)
        f_db = '{}/.config/feed2social/feed2facebook.sqlite3'.format(home)

        c = configparser.ConfigParser()
        c.read(f_conf)

        if 'sentry_sdk_url' in c['default'] and '' != c['default']['sentry_sdk_url']:
            sentry_sdk_url = c['default']['sentry_sdk_url']
            sentry_sdk.init(sentry_sdk_url)

        feed_url = c['default']['feed_url']
        feed = feedparser.parse(feed_url)
        items = feed.entries

        s = sqlite3.connect(f_db)

        sql_insert = 'INSERT INTO entry (entry_id, created_at) VALUES (?, ?);'
        sql_select = 'SELECT COUNT(*) FROM entry WHERE entry_id = ?;'

        # Workaround: cannot use allow_tags=[]:
        cl = Cleaner(allow_tags=[''])

        for item in items:
            # Print out details.
            print('* item = {}'.format(item))

            # Craft "text".
            #
            # First to remove all tags except "a" and root's "div".
            text = cl.clean_html(item['content_html'])

            # Remove root's "div".
            text = text.replace('<div>', '').replace('</div>', '')

            # Generate parameters.
            id_str = item['id']
            url = item['id']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = '{}\n\n{}'.format(text, url)
                print('* content = {}'.format(content))

                print(content)
                self.post(content)

                c.execute(sql_insert, (id_str, int(time.time())))
                s.commit()

        self.quit_browser()

    def quit_browser(self):
        if self.b is None:
            return

        self.b.quit()

if __name__ == '__main__':
    Feed2Facebook().main()