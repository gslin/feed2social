#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import configparser
import datetime
import feedparser
import html
import io
import json
import os
import sentry_sdk
import sqlite3
import time

from contextlib import closing, redirect_stderr, redirect_stdout
from curl_cffi.requests import Session
from lxml.html.clean import Cleaner
from scripts.fb_config import POST_DOC_ID
from scripts.fb_session import COOKIE_KEYS, fetch_tokens, graphql_request
from scripts.post import build_variables

def tprint(*args, **kwargs):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('[%Y-%m-%dT%H:%M:%SZ]')
    print(timestamp, *args, **kwargs)

class Feed2Facebook(object):
    _config = None

    @property
    def config(self):
        if self._config is None:
            home = os.environ['HOME']
            f_conf = '{}/.config/feed2social/config.ini'.format(home)

            self._config = configparser.ConfigParser()
            self._config.read(f_conf)
        return self._config

    def load_cookies(self):
        path = os.path.expanduser(self.config['default'].get(
            'facebook_cookies_file', '~/.config/feed2social/facebook-cookies.json',
        ))
        try:
            with open(path, encoding='utf-8') as f:
                exported = json.load(f)
        except (OSError, ValueError):
            raise configparser.Error(
                'Cannot read facebook_cookies_file. Export Facebook cookies as JSON '
                'to ~/.config/feed2social/facebook-cookies.json or configure its path.'
            ) from None

        if not isinstance(exported, list):
            raise configparser.Error('facebook_cookies_file must contain a JSON array of cookies.')

        cookies = {}
        for item in exported:
            if not isinstance(item, dict) or not all(
                isinstance(item.get(key), str) for key in ('name', 'value', 'domain')
            ):
                raise configparser.Error('Each exported cookie needs name, value, and domain strings.')
            domain = item['domain'].lstrip('.').lower()
            if domain == 'facebook.com' or domain.endswith('.facebook.com'):
                if item['name'] in COOKIE_KEYS and item['value']:
                    cookies[item['name']] = item['value']

        if not cookies.get('c_user') or not cookies.get('xs'):
            raise configparser.Error('Facebook cookies must include c_user and xs for facebook.com.')
        return cookies

    @staticmethod
    def call_fbpost(operation, function, *args):
        # Upstream CLI helpers print response bodies on errors and call sys.exit().
        # Keep session data out of logs and turn those exits into posting failures.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return function(*args)
            except SystemExit:
                raise RuntimeError(
                    f'fbpost could not {operation}. Check Facebook cookies and '
                    'facebook_post_doc_id; the entry was not recorded.'
                ) from None

    def post(self, text):
        config = self.config['default']
        privacy = config.get('facebook_privacy', 'EVERYONE').strip().upper()
        if privacy not in ('SELF', 'FRIENDS', 'EVERYONE'):
            raise configparser.Error('facebook_privacy must be SELF, FRIENDS, or EVERYONE.')
        doc_id = config.get('facebook_post_doc_id', POST_DOC_ID).strip()
        if not doc_id or not doc_id.isascii() or not doc_id.isdigit():
            raise configparser.Error('facebook_post_doc_id must be a numeric GraphQL document ID.')

        cookies = self.load_cookies()
        actor_id = cookies.get('i_user') or cookies['c_user']
        # Use curl_cffi's matching browser headers instead of fbpost's Chrome 134 headers.
        with Session(impersonate='chrome124', allow_redirects=False, headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }) as session:
            for name, value in cookies.items():
                session.cookies.set(name, value, domain='.facebook.com', secure=True)
            tokens = self.call_fbpost('fetch login tokens', fetch_tokens, session)
            variables = build_variables(text, actor_id, privacy)
            result = self.call_fbpost(
                'publish the post', graphql_request, session, tokens, actor_id,
                doc_id, 'ComposerStoryCreateMutation', variables,
            )

        if not isinstance(result, dict) or result.get('errors'):
            raise RuntimeError('Facebook rejected the post; check cookies and facebook_post_doc_id.')
        story = result
        for key in ('data', 'story_create', 'story'):
            story = story.get(key) if isinstance(story, dict) else None
        if isinstance(story, dict):
            for key in ('id', 'url'):
                value = story.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        raise RuntimeError('Facebook returned no post ID or URL; the entry was not recorded.')

    def main(self, sync_only=False):
        tprint('* Started.')

        if sync_only:
            tprint('* sync_only mode: will not post to Facebook')

        home = os.environ['HOME']
        f_db = '{}/.config/feed2social/feed2facebook.sqlite3'.format(home)

        c = self.config
        if 'sentry_sdk_url' in c['default'] and '' != c['default']['sentry_sdk_url']:
            sentry_sdk_url = c['default']['sentry_sdk_url']
            sentry_sdk.init(sentry_sdk_url)

        feed_url = c['default']['feed_url']
        feed = feedparser.parse(feed_url)
        items = feed.entries

        sql_insert = 'INSERT INTO entry (entry_id, created_at) VALUES (?, ?);'
        sql_select = 'SELECT COUNT(*) FROM entry WHERE entry_id = ?;'

        # Workaround: cannot use allow_tags=[]:
        cl = Cleaner(allow_tags=['p'])

        with closing(sqlite3.connect(f_db)) as s:
            for item in reversed(items):
                text = item['description']

                # Print out item's id.
                tprint('* item.id = {}'.format(item.id))

                # Remove all tags except paragraphs and the root div.
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
                    tprint('* content = {}'.format(content))

                    if sync_only:
                        tprint('* sync_only: skipping post to Facebook')
                        c.execute(sql_insert, (id_str, int(time.time())))
                        s.commit()
                        continue

                    post_id = self.post(content)
                    tprint('* Facebook post = {}'.format(post_id))

                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync feed to Facebook')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Facebook')
    args = parser.parse_args()

    Feed2Facebook().main(sync_only=args.sync_only)
