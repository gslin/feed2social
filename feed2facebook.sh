#!/bin/bash

#
# Support mise & pyenv & native environment:
if command -v mise; then
    eval "$(mise activate bash)"
elif [[ -d "${HOME}/.pyenv" ]]; then
    export PATH="${HOME}/.pyenv/shims:${HOME}/.pyenv/bin:${PATH}"
    eval "$(pyenv init -)"
fi

BASEDIR="$(dirname $0)"
LANG=en_US.UTF-8 "${BASEDIR}/feed2facebook.py" || true

# Since our pkill run with parent pid filtering (ppid == 1), we need to
# kill geckodriver first, then firefox-esr afterwards.
pkill -P 1 geckodriver || true
pkill -P 1 firefox-esr || true
