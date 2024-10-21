#!/bin/bash

#
# Support mise & pyenv & native environment:
if command -v mise > /dev/null; then
    eval "$(mise activate bash --shims)"
elif [[ -d "${HOME}/.pyenv" ]]; then
    export PATH="${HOME}/.pyenv/shims:${HOME}/.pyenv/bin:${PATH}"
    eval "$(pyenv init -)"
fi

cd "$(dirname $0)"
LANG=en_US.UTF-8 ./feed2bluesky.py || true
