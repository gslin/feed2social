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
import re
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

def response_shape(value):
    """Keep JSON structure without exposing response text, IDs, or tokens."""
    if isinstance(value, dict):
        return {key: response_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [response_shape(item) for item in value]
    if isinstance(value, str):
        return '<string: {} chars>'.format(len(value))
    if value is None or isinstance(value, bool):
        return value
    return '<{}>'.format(type(value).__name__)

def debug_login_response(response, tokens):
    if response is None:
        tprint('* Facebook login: no HTTP response received for the homepage GET')
        return
    content_type = response.headers.get('Content-Type', '').split(';', 1)[0]
    tprint('* Facebook login: HTTP {}, content-type={}, body_bytes={}, redirect={}'.format(
        response.status_code, json.dumps(content_type), len(response.content),
        bool(response.headers.get('Location')),
    ))
    # Report only fixed marker names and booleans, never page content or URLs.
    markers = {name: name in response.text for name in ('DTSGInitData', 'fb_dtsg', 'LSD')}
    tprint('* Facebook login: page markers = {}'.format(json.dumps(markers)))
    # Decode only the known module payloads; report token types/lengths, not values.
    # A marker alone cannot distinguish an empty token from incompatible formatting.
    modules = {}
    for name in ('DTSGInitData', 'LSD'):
        modules[name] = []
        pattern = r'"' + name + r'"\s*,\s*\[\s*\]\s*,\s*'
        for match in re.finditer(pattern, response.text):
            try:
                data, _ = json.JSONDecoder().raw_decode(response.text, match.end())
            except ValueError:
                modules[name].append('<unrecognized JSON>')
                continue
            if isinstance(data, dict):
                fields = {}
                for key in ('token', 'async_get_token'):
                    if key in data:
                        value = data[key]
                        fields[key] = (response_shape(value)
                                       if value is None or isinstance(value, (str, bool))
                                       else '<{}>'.format(type(value).__name__))
                modules[name].append(fields)
            else:
                modules[name].append('<unexpected payload type>')
    tprint('* Facebook login: token fields = {}'.format(json.dumps(modules)))
    tprint('* Facebook login: fb_dtsg extracted = {}'.format(
        bool(tokens and tokens.get('fb_dtsg')),
    ))

def debug_post_response(response):
    if response is None:
        tprint('* Facebook debug: no HTTP response received for the POST')
        return
    content_type = response.headers.get('Content-Type', '').split(';', 1)[0]
    tprint('* Facebook debug: HTTP {}, content-type={}, body_bytes={}'.format(
        response.status_code, json.dumps(content_type), len(response.content),
    ))
    body = response.text.lstrip()
    if body.startswith('for (;;);'):
        body = body[len('for (;;);'):]
    # fbpost selects only the first data-bearing line; show all frames here.
    try:
        frames = [json.loads(body)]
    except ValueError:
        lines = [line for line in body.splitlines() if line.strip()]
        frames = []
        for number, line in enumerate(lines, 1):
            try:
                frames.append(json.loads(line))
            except ValueError:
                tprint('* Facebook debug: non-JSON line {} ({} chars, content omitted)'.format(
                    number, len(line),
                ))
    tprint('* Facebook debug: {} JSON frame(s)'.format(len(frames)))
    for number, frame in enumerate(frames, 1):
        tprint('* Facebook debug: response JSON #{} = {}'.format(
            number, json.dumps(response_shape(frame), ensure_ascii=False),
        ))

class FacebookSession(Session):
    last_get_response = None
    last_post_response = None

    def get(self, *args, **kwargs):
        self.last_get_response = None
        self.last_get_response = super().get(*args, **kwargs)
        return self.last_get_response

    def post(self, *args, **kwargs):
        self.last_post_response = None
        self.last_post_response = super().post(*args, **kwargs)
        return self.last_post_response

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
        config = self.config['default']
        # Cookie values can contain percent escapes; do not interpolate them.
        cookies = {
            name: config.get('facebook_' + name, raw=True).strip()
            for name in COOKIE_KEYS if 'facebook_' + name in config
        }
        if cookies:
            if not cookies.get('c_user') or not cookies.get('xs'):
                raise configparser.Error(
                    'Set both facebook_c_user and facebook_xs in config.ini '
                    'when providing Facebook cookies there.'
                )
            return {name: value for name, value in cookies.items() if value}

        path = os.path.expanduser(config.get(
            'facebook_cookies_file', '~/.config/feed2social/facebook-cookies.json',
        ))
        try:
            with open(path, encoding='utf-8') as f:
                exported = json.load(f)
        except (OSError, ValueError):
            raise configparser.Error(
                'Set facebook_c_user and facebook_xs in config.ini, or export Facebook '
                'cookies as JSON to ~/.config/feed2social/facebook-cookies.json '
                '(overridable with facebook_cookies_file). Cannot read the cookie file.'
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
    def call_fbpost(operation, function, *args, error_hint=None):
        # Upstream CLI helpers print response bodies on errors and call sys.exit().
        # Keep session data out of logs and turn those exits into posting failures.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return function(*args)
            except SystemExit:
                if error_hint is None:
                    error_hint = ('Check Facebook cookies and facebook_post_doc_id; '
                                  'the entry was not recorded.')
                raise RuntimeError(
                    f'fbpost could not {operation}. {error_hint}'
                ) from None

    @staticmethod
    def create_session(cookies):
        # Use curl_cffi's matching browser headers instead of fbpost's Chrome 134 headers.
        session = FacebookSession(impersonate='chrome124', allow_redirects=False, headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        for name, value in cookies.items():
            session.cookies.set(name, value, domain='.facebook.com', secure=True)
        return session

    def fetch_login_tokens(self, session, debug=False):
        tokens = None
        try:
            tokens = self.call_fbpost('fetch login tokens', fetch_tokens, session, error_hint=(
                'Could not extract fb_dtsg from the Facebook homepage. '
                'Check the login diagnostics above; open Facebook in a browser to check '
                'for login or account verification, and refresh cookies if needed. '
                'Run --check-login to check again without posting. '
                'No post was sent; the entry was not recorded.'
            ))
            return tokens
        finally:
            if debug or tokens is None:
                debug_login_response(session.last_get_response, tokens)

    def post(self, text, debug=False):
        config = self.config['default']
        privacy = config.get('facebook_privacy', 'EVERYONE').strip().upper()
        if privacy not in ('SELF', 'FRIENDS', 'EVERYONE'):
            raise configparser.Error('facebook_privacy must be SELF, FRIENDS, or EVERYONE.')
        doc_id = config.get('facebook_post_doc_id', POST_DOC_ID).strip()
        if not doc_id or not doc_id.isascii() or not doc_id.isdigit():
            raise configparser.Error('facebook_post_doc_id must be a numeric GraphQL document ID.')

        cookies = self.load_cookies()
        actor_id = cookies.get('i_user') or cookies['c_user']
        with self.create_session(cookies) as session:
            tokens = self.fetch_login_tokens(session, debug=debug)
            variables = build_variables(text, actor_id, privacy)
            try:
                result = self.call_fbpost(
                    'publish the post', graphql_request, session, tokens, actor_id,
                    doc_id, 'ComposerStoryCreateMutation', variables,
                )
            finally:
                if debug:
                    debug_post_response(session.last_post_response)

        if debug:
            tprint('* Facebook debug: fbpost result = {}'.format(
                json.dumps(response_shape(result), ensure_ascii=False),
            ))

        if not isinstance(result, dict) or result.get('errors'):
            raise RuntimeError('Facebook rejected the post; check cookies and facebook_post_doc_id.')
        created = result
        for key in ('data', 'story_create'):
            created = created.get(key) if isinstance(created, dict) else None
        if isinstance(created, dict):
            # Facebook can return creation IDs while the expanded story is null.
            identifiers = [created.get('post_id'), created.get('story_id')]
            story = created.get('story')
            if isinstance(story, dict):
                identifiers.extend([story.get('id'), story.get('url')])
            for value in identifiers:
                if isinstance(value, str) and value.strip():
                    return value
        raise RuntimeError('Facebook returned no post ID or URL; the entry was not recorded.')

    def main(self, sync_only=False, debug=False, check_login=False):
        tprint('* Started.')

        if check_login:
            with self.create_session(self.load_cookies()) as session:
                self.fetch_login_tokens(session, debug=True)
            tprint('* Facebook login-token check passed; posting was not tested.')
            return

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

                    post_id = self.post(content, debug=debug)
                    tprint('* Facebook post = {}'.format(post_id))

                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                    break

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync feed to Facebook')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--sync-only', action='store_true',
                      help='Only sync feed to database without posting to Facebook')
    mode.add_argument('--check-login', action='store_true',
                      help='Check Facebook login tokens without reading the feed or database')
    parser.add_argument('--debug', action='store_true',
                        help='Print redacted Facebook login and POST details (still posts normally)')
    args = parser.parse_args()

    Feed2Facebook().main(sync_only=args.sync_only, debug=args.debug, check_login=args.check_login)
