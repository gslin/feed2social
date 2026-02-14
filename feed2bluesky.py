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

import lxml.html

from atproto import Client, client_utils, models
from lxml.html.clean import Cleaner

def tprint(*args, **kwargs):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('[%Y-%m-%dT%H:%M:%SZ]')
    print(timestamp, *args, **kwargs)


def fetch_og_metadata(url):
    """Fetch Open Graph metadata from a URL.
    Returns dict with keys: title, description, image_url (any can be None).
    """
    try:
        res = httpx.get(url, timeout=15.0, follow_redirects=True)
        res.raise_for_status()
        doc = lxml.html.fromstring(res.text)

        og_title = None
        og_description = None
        og_image = None

        el = doc.xpath('//meta[@property="og:title"]/@content')
        if el:
            og_title = el[0]

        el = doc.xpath('//meta[@property="og:description"]/@content')
        if el:
            og_description = el[0]

        el = doc.xpath('//meta[@property="og:image"]/@content')
        if el:
            og_image = el[0]

        # Fallback to <title> and <meta name="description">
        if not og_title:
            el = doc.xpath('//title/text()')
            if el:
                og_title = el[0]

        if not og_description:
            el = doc.xpath('//meta[@name="description"]/@content')
            if el:
                og_description = el[0]

        return {
            'title': og_title,
            'description': og_description,
            'image_url': og_image,
        }
    except Exception as e:
        tprint('* Exception fetching OG metadata: {}'.format(e))
        return {}


def create_external_embed(client, url, og_data, feed_title):
    """Create an AppBskyEmbedExternal embed for the link card.
    Returns embed object, or None if creation fails.
    """
    try:
        title = og_data.get('title') or feed_title or url
        description = og_data.get('description') or ''

        thumb = None
        image_url = og_data.get('image_url')
        if image_url:
            try:
                tprint('* Downloading OG image: {}'.format(image_url))
                img_res = httpx.get(image_url, timeout=15.0, follow_redirects=True)
                img_res.raise_for_status()
                upload = client.upload_blob(img_res.content)
                thumb = upload.blob
                tprint('* OG image uploaded: {} bytes'.format(len(img_res.content)))
            except Exception as e:
                tprint('* Exception downloading/uploading OG image: {}'.format(e))

        embed = models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                title=title,
                description=description,
                uri=url,
                thumb=thumb,
            )
        )
        return embed
    except Exception as e:
        tprint('* Exception creating external embed: {}'.format(e))
        return None

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
            self._client = Client()
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

    def main(self, sync_only=False):
        tprint('* Started.')

        if sync_only:
            tprint('* sync_only mode: will not post to Bluesky')

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
            tprint('* item.id = {}'.format(item.id))

            # Check if entry has media content (images)
            image_url = None
            if hasattr(item, 'media_content'):
                for media in item.media_content:
                    if media.get('type', '').startswith('image/'):
                        image_url = media.get('url')
                        break

            # Skip if body is empty and no image.
            if (not body or not body.strip()) and not image_url:
                tprint('* Skipping: empty body and no image')
                continue

            # Craft "body".
            #
            # First to remove all tags except "a" and root's "div".
            if body and body.strip():
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
            else:
                body = ''

            # Generate parameters.
            id_str = item['id']
            url = item['link']

            c = s.cursor()

            c.execute(sql_select, (id_str, ))
            if 0 == c.fetchone()[0]:
                content = body
                tprint('* content = {}'.format(content))

                if sync_only:
                    tprint('* sync_only: skipping post to Bluesky')
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                    continue

                # Download image if present
                image_data = None
                if image_url:
                    try:
                        tprint('* Downloading image: {}'.format(image_url))
                        img_res = httpx.get(image_url, timeout=30.0)
                        if img_res.status_code == 200:
                            image_data = img_res.content
                            tprint('* Image downloaded: {} bytes'.format(len(image_data)))
                        else:
                            tprint('* Failed to download image: {}'.format(img_res.status_code))
                    except Exception as e:
                        tprint('* Exception downloading image: {}'.format(e))

                # Post to Bluesky
                if image_data:
                    # Post with image using send_image
                    tb = client_utils.TextBuilder()

                    # Handle links
                    http_pattern = re.compile(r'^https?://[^\s]+')
                    for w in re.split(r'(https?://[^\s]+)', content):
                        if len(w) == 0:
                            continue

                        if http_pattern.match(w):
                            tb.link(w, w)
                        else:
                            tb.text(w)

                    post = self.client.send_image(text=tb, image=image_data, image_alt='')
                else:
                    # Post text only with link card embed
                    tb = client_utils.TextBuilder()

                    # Handle links
                    http_pattern = re.compile(r'^https?://[^\s]+')
                    for w in re.split(r'(https?://[^\s]+)', content):
                        if len(w) == 0:
                            continue

                        if http_pattern.match(w):
                            tb.link(w, w)
                        else:
                            tb.text(w)

                    # Fetch OG metadata and create link card embed
                    og_data = fetch_og_metadata(url)
                    feed_title = html.unescape(item.get('title', ''))
                    embed = create_external_embed(self.client, url, og_data, feed_title)

                    post = self.client.send_post(tb, embed=embed)

                tprint('* type(post) = {}'.format(type(post)))
                tprint('* post = {}'.format(post))
                if isinstance(post, object) and post.cid:
                    c.execute(sql_insert, (id_str, int(time.time())))
                    s.commit()
                else:
                    s.rollback()

                tb2 = client_utils.TextBuilder()
                tb2.text('Sync from: ')
                tb2.link(url, url)

                post_ref = models.create_strong_ref(post)
                reply = self.client.send_post(tb2, reply_to=models.AppBskyFeedPost.ReplyRef(parent=post_ref, root=post_ref))
                tprint('* type(reply) = {}'.format(type(reply)))
                tprint('* reply = {}'.format(reply))

if '__main__' == __name__:
    parser = argparse.ArgumentParser(description='Sync feed to Bluesky')
    parser.add_argument('--sync-only', action='store_true',
                        help='Only sync feed to database without posting to Bluesky')
    args = parser.parse_args()

    t = Feed2Bluesky()
    t.main(sync_only=args.sync_only)
