#!/usr/bin/env python3

import configparser
import datetime
import os
import httpx

def tprint(*args, **kwargs):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('[%Y-%m-%dT%H:%M:%SZ]')
    print(timestamp, *args, **kwargs)

def main():
    tprint('* Started.')

    home = os.environ['HOME']
    f_conf = '{}/.config/feed2social/config.ini'.format(home)

    config = configparser.ConfigParser()
    config.read(f_conf)

    access_token = config['default']['threads_access_token']

    # Refresh the long-lived access token.
    res = httpx.get('https://graph.threads.net/refresh_access_token', params={
        'grant_type': 'th_refresh_token',
        'access_token': access_token,
    })
    tprint('* res = {}'.format(res))
    tprint('* res.text = {}'.format(res.text))

    if res.status_code != 200:
        tprint('* Failed to refresh access token.')
        return

    new_access_token = res.json()['access_token']
    expires_in = res.json()['expires_in']

    tprint('* new_access_token = {}'.format(new_access_token))
    tprint('* expires_in = {} seconds ({} days)'.format(expires_in, expires_in // 86400))

    # Update the config file.
    config['default']['threads_access_token'] = new_access_token

    with open(f_conf, 'w') as f:
        config.write(f)

    tprint('* Config file updated.')

if '__main__' == __name__:
    main()
