#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import datetime
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
from selenium.webdriver.firefox.service import Service

class Feed2Facebook(object):
    _config = None
    b = None

    @property
    def config(self):
        if self._config is None:
            home = os.environ['HOME']
            f_conf = '{}/.config/feed2social/config.ini'.format(home)

            self._config = configparser.ConfigParser()
            self._config.read(f_conf)
        return self._config

    @property
    def facebook_username(self):
        return self._config['default']['facebook_username']

    def init_browser(self):
        if self.b is not None:
            return

        home = os.environ['HOME']

        service = Service('/usr/bin/geckodriver')

        options = selenium.webdriver.FirefoxOptions()
        options.binary_location = '/usr/bin/firefox-esr'
        options.add_argument('-headless')

        # Workaround to specify profile.
        # via: https://github.com/SeleniumHQ/selenium/issues/11028
        options.add_argument('-profile')
        options.add_argument(home + '/.mozilla/firefox-esr/selenium')

        self.b = selenium.webdriver.Firefox(service=service, options=options)

    def post(self, text):
        self.init_browser()

        b = self.b
        url = 'https://www.facebook.com/{}'.format(self.facebook_username)

        b.get(url)
        time.sleep(1)

        # click to popup
        t = b.find_element(by=By.CSS_SELECTOR, value='a[aria-label] + div[role="button"][tabindex="0"]')
        t.click()
        time.sleep(2)

        # input
        t = b.find_element(by=By.CSS_SELECTOR, value='div[role="dialog"] div[role="textbox"]')
        t.click()
        time.sleep(1)
        for c in text:
            t.send_keys(c)
        time.sleep(1)

        # click "Next"
        btn = b.find_element(by=By.CSS_SELECTOR, value='div[role="dialog"] div[aria-label="Next"]')
        btn.click()
        time.sleep(1)

        # click "Post"
        btn = b.find_element(by=By.CSS_SELECTOR, value='div[role="dialog"] div[aria-label="Post"]')
        btn.click()
        time.sleep(1)

    def main(self):
        print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

        home = os.environ['HOME']
        f_db = '{}/.config/feed2social/feed2facebook.sqlite3'.format(home)

        c = self.config
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
        cl = Cleaner(allow_tags=['p'])

        for item in reversed(items):
            text = item['description']

            # Print out item's id.
            print('* item.id = {}'.format(item.id))

            # Craft "text".
            #
            # First to remove all tags except "a" and root's "div".
            text = cl.clean_html(text)

            # Skip if there is "#nofb" tag.
            if '#nofb' in text:
                continue

            # Remove root's "div".
            text = text.replace('<div>', '').replace('</div>', '')

            # <p> and </p>
            text = text.replace('<p>', '\n').replace('</p>', '\n')

            # trim
            text = text.strip()

            # unescape
            text = html.unescape(text)

            # Generate parameters.
            id_str = item['id']
            url = item['link']

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
