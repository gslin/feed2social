#!/usr/bin/env python3

import configparser
import datetime
import os
import requests

def main():
    print('* datetime.datetime.now() = {}'.format(datetime.datetime.now()))

    home = os.environ['HOME']
    f_conf = '{}/.config/feed2social/config.ini'.format(home)

    config = configparser.ConfigParser()
    config.read(f_conf)

    access_token = config['default']['threads_access_token']

    # Refresh the long-lived access token.
    res = requests.get('https://graph.threads.net/refresh_access_token', params={
        'grant_type': 'th_refresh_token',
        'access_token': access_token,
    })
    print('* res = {}'.format(res))
    print('* res.text = {}'.format(res.text))

    if res.status_code != 200:
        print('* Failed to refresh access token.')
        return

    new_access_token = res.json()['access_token']
    expires_in = res.json()['expires_in']

    print('* new_access_token = {}'.format(new_access_token))
    print('* expires_in = {} seconds ({} days)'.format(expires_in, expires_in // 86400))

    # Update the config file.
    config['default']['threads_access_token'] = new_access_token

    with open(f_conf, 'w') as f:
        config.write(f)

    print('* Config file updated.')

if '__main__' == __name__:
    main()
