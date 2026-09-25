#!/bin/sh
set -eu
i=0
until alembic upgrade head; do
  i=$((i + 1))
  if [ "$i" -ge 5 ]; then
    echo "alembic upgrade head failed" >&2
    exit 1
  fi
  sleep 2
done
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec python -m src.main
