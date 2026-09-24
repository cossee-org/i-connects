#!/bin/sh
# Start the web service: wait for the database, apply migrations, then exec
# the server so it -- not this shell -- is the process that receives SIGTERM.
#
#   scripts/start-web.sh gunicorn     production (docker-compose.prod.yml, image CMD)
#   scripts/start-web.sh runserver    development (docker-compose.yml)
#
# Why the wait loop: at daemon start (host reboot) Docker restarts containers
# by policy and ignores depends_on, and even under `compose up` the very first
# connection can race the container's network setup -- seen live as
# "failed to resolve host 'db': Temporary failure in name resolution". A
# container that exits on that takes celery/celery-beat down with it
# ("dependency failed to start"); one that retries just turns healthy a few
# seconds later. Migration errors are NOT retried: only the connection is.
#
#   DB_WAIT_TIMEOUT   total seconds to keep trying (default 60) -- a deadline, not
#                     an attempt count, because a resolver that times out makes
#                     every attempt slow and the container must not sit in this
#                     loop past the health check's start_period.
#   DB_WAIT_SECONDS   pause between attempts (default 2)
set -eu

mode="${1:-gunicorn}"
timeout="${DB_WAIT_TIMEOUT:-60}"
delay="${DB_WAIT_SECONDS:-2}"

case "$mode" in
  gunicorn|runserver) ;;
  *)
    echo "start-web: unknown mode '$mode' (expected gunicorn or runserver)" >&2
    exit 2
    ;;
esac

db_ready() {
  python - <<'PY'
import sys

import django

django.setup()
from django.db import connection  # noqa: E402

try:
    connection.ensure_connection()
except Exception as exc:  # noqa: BLE001 -- any failure means "not yet"
    reason = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    print(f"start-web: database not reachable yet: {reason}", file=sys.stderr)
    sys.exit(1)
PY
}

deadline=$(( $(date +%s) + timeout ))
until db_ready; do
  now=$(date +%s)
  if [ "$now" -ge "$deadline" ]; then
    echo "start-web: database still unreachable after ${timeout}s; giving up" >&2
    exit 1
  fi
  echo "start-web: retrying in ${delay}s ($(( deadline - now ))s left)" >&2
  sleep "$delay"
done

python manage.py migrate --noinput

case "$mode" in
  gunicorn)
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout 60 --graceful-timeout 30 \
      --access-logfile - --error-logfile -
    ;;
  runserver)
    exec python manage.py runserver 0.0.0.0:8000
    ;;
esac
