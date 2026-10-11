#!/bin/sh
set -e

case "$1" in
  api)
    alembic upgrade head
    exec uvicorn releasewatch.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
    ;;
  worker)
    exec python -m releasewatch.worker
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
