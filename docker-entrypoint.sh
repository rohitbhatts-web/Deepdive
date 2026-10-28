#!/bin/sh
# The demo database is built into the image and owned by root, so the app needs no writable directory: drop privileges and run.
# (Render may still mount the older deployment's disk at /data; this version neither reads nor changes it.)
set -e
if [ "$(id -u)" = "0" ]; then
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
