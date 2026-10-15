#!/bin/sh
set -eu
if [ -z "${RIIRIITUBE_URL:-}" ]; then
    echo 'Set RIIRIITUBE_URL to the HTTP address your Wii can reach, for example http://192.168.1.50:301' >&2
    exit 1
fi
exec "$@"
