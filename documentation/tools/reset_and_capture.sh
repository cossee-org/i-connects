#!/usr/bin/env bash
# Rebuild the user-guide screenshots from an empty database.
#
# Expects the site's database to be reachable via DATABASE_URL, a Django dev
# server and a Celery worker to be (re)started by this script with
# docs_settings, and Playwright + Chromium installed. See README.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONPATH="documentation/tools${PYTHONPATH:+:$PYTHONPATH}"
export DJANGO_SETTINGS_MODULE=docs_settings
export DOCS_MAIL_DIR="${DOCS_MAIL_DIR:-/tmp/iconnects-docs-mail}"

python manage.py flush --noinput
python manage.py migrate -v0
mkdir -p "$DOCS_MAIL_DIR" && rm -f "$DOCS_MAIL_DIR"/*.log
rm -f documentation/screenshots/*.png

python manage.py runserver 0.0.0.0:8000 > /tmp/iconnects-docs-runserver.log 2>&1 &
SERVER=$!
celery -A config worker -l warning > /tmp/iconnects-docs-celery.log 2>&1 &
WORKER=$!
trap 'kill $SERVER $WORKER 2>/dev/null || true' EXIT
until curl -fs http://localhost:8000/healthz/ >/dev/null; do sleep 1; done

python documentation/tools/capture_screenshots.py "$@"
