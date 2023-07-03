#!/bin/bash

LANG=en_US.UTF-8 \
    ~/.pyenv/shims/python3 ~/git/feed2social/feed2facebook.py || \
    true

# Since our pkill run with parent pid filtering (ppid == 1), we need to
# kill geckodriver first, then firefox-esr afterwards.
pkill -P 1 geckodriver || true
pkill -P 1 firefox-esr || true
