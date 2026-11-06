#!/bin/bash

# Include user installs when cron runs without mise activation.
export PATH="${PATH:-/usr/local/bin:/usr/bin:/bin}:$HOME/.local/bin:${MISE_DATA_DIR:-$HOME/.local/share/mise}/installs/uv/latest/.mise-bins"

cd -- "$(dirname -- "$0")" || exit 1
LANG=en_US.UTF-8 exec uv run --locked python ./feed2twitter.py "$@"
