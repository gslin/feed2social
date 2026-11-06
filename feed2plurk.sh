#!/bin/bash

cd -- "$(dirname -- "$0")" || exit 1
LANG=en_US.UTF-8 exec ./.venv/bin/python ./feed2plurk.py "$@"
