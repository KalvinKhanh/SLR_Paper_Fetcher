#!/bin/sh
set -eu

mkdir -p /app/data /app/downloads /app/.vnu-browser-profile
if [ ! -f "$APP_ENV_FILE" ]; then
    cp /app/docker/defaults.env "$APP_ENV_FILE"
fi

exec "$@"
