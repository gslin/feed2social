#!/bin/bash

cd -- "$(dirname -- "$0")" || exit 1
LANG=en_US.UTF-8 ./.venv/bin/python ./feed2facebook.py "$@"
script_status=$?

# Since our pkill run with parent pid filtering (ppid == 1), we need to
# kill geckodriver first, then firefox-esr afterwards.
pkill -P 1 geckodriver || true
pkill -P 1 firefox-esr || true

exit "$script_status"
