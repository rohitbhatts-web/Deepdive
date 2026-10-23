#!/bin/sh
# Render mounts a persistent disk owned by root, and the app must not run as root: fix the data directory, then drop privileges.
set -e
if [ "$(id -u)" = "0" ]; then
  data_dir="$(dirname "${DATABASE_PATH:-/data/deepdive.db}")"
  mkdir -p "$data_dir"
  chown app:app "$data_dir"
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
