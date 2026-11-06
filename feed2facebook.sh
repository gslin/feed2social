#!/bin/bash

# Include user installs when cron runs without mise activation.
export PATH="${PATH:-/usr/local/bin:/usr/bin:/bin}:$HOME/.local/bin:${MISE_DATA_DIR:-$HOME/.local/share/mise}/installs/uv/latest/.mise-bins"

cd -- "$(dirname -- "$0")" || exit 1
LANG=en_US.UTF-8 uv run --locked python ./feed2facebook.py "$@"
script_status=$?

# Since our pkill run with parent pid filtering (ppid == 1), we need to
# kill geckodriver first, then firefox-esr afterwards.
pkill -P 1 geckodriver || true
pkill -P 1 firefox-esr || true

exit "$script_status"
