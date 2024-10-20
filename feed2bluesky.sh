#!/bin/bash

#
# Support mise & pyenv & native environment:
if command -v mise > /dev/null; then
    eval "$(mise activate bash)"
elif [[ -d "${HOME}/.pyenv" ]]; then
    export PATH="${HOME}/.pyenv/shims:${HOME}/.pyenv/bin:${PATH}"
    eval "$(pyenv init -)"
fi

BASEDIR="$(dirname $0)"
LANG=en_US.UTF-8 "${BASEDIR}/feed2bluesky.py" || true
