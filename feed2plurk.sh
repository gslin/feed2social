#!/bin/bash

#
# Support mise & pyenv & native environment:
if command -v mise; then
    eval "$(mise activate bash)"
elif [[ -d "${HOME}/.pyenv"]]; then
    export PATH="${HOME}/.pyenv/shims:${HOME}/.pyenv/bin:${PATH}"
    eval "$(pyenv init -)"
fi

BASEDIR="$(dirname $0)"
"${BASEDIR}/feed2plurk.py" || true
