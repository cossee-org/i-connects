#!/usr/bin/env bash
# Restore a scripts/backup.sh backup.
#
#   scripts/restore.sh --db  ~/backups/db-<stamp>.dump \
#                      [--media ~/backups/media-<stamp>.tar.gz] \
#                      [--target DBNAME] --yes
#
# Without --target the LIVE database is replaced: web, celery and celery-beat
# are stopped for the duration, pg_restore --clean runs against the
# production database, the media tarball (if given) is extracted over
# /app/media, and the app is brought back up.
#
# With --target a fresh database of that name is created on the same server
# and the dump is restored into it; production is not touched and --media is
# only list-verified. This is the restore drill (DEPLOYMENT.md Part K).
#
# COMPOSE_FILES overrides the `docker compose` -f flags (default: dev + prod).
set -euo pipefail

COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.prod.yml}"
DB_DUMP=""
MEDIA_TAR=""
TARGET=""
YES=0

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)     DB_DUMP="${2:-}"; shift 2 ;;
    --media)  MEDIA_TAR="${2:-}"; shift 2 ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --yes)    YES=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

[[ -n "$DB_DUMP" || -n "$MEDIA_TAR" ]] || { echo "nothing to restore: pass --db and/or --media" >&2; usage; }
[[ -z "$DB_DUMP" || -r "$DB_DUMP" ]] || { echo "cannot read $DB_DUMP" >&2; exit 1; }
[[ -z "$MEDIA_TAR" || -r "$MEDIA_TAR" ]] || { echo "cannot read $MEDIA_TAR" >&2; exit 1; }
if [[ -n "$TARGET" && ! "$TARGET" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "--target must be a plain identifier (letters, digits, underscore)" >&2; exit 1
fi
if [[ "$YES" -ne 1 ]]; then
  if [[ -n "$TARGET" ]]; then
    echo "Would restore into a NEW database '$TARGET' (production untouched). Re-run with --yes." >&2
  else
    echo "Would REPLACE the live production database and media. Re-run with --yes." >&2
  fi
  exit 1
fi

compose() {
  # shellcheck disable=SC2086  # COMPOSE_FILES is a list of flags on purpose
  docker compose $COMPOSE_FILES "$@"
}

log() { printf '%s restore: %s\n' "$(date -Is)" "$*"; }

status=0

if [[ -n "$TARGET" ]]; then
  # ---- Drill: restore into a fresh, separately named database -------------
  if [[ -n "$DB_DUMP" ]]; then
    log "creating database $TARGET"
    compose exec -T db sh -c 'exec createdb -U "$POSTGRES_USER" "$1"' sh "$TARGET"
    log "restoring $DB_DUMP into $TARGET"
    compose exec -T db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-privileges' sh "$TARGET" \
      < "$DB_DUMP" || status=$?
    log "done. Inspect with: docker compose exec -T db psql -U <user> -d $TARGET"
    log "then drop it:      docker compose exec -T db dropdb -U <user> $TARGET"
  fi
  if [[ -n "$MEDIA_TAR" ]]; then
    log "drill mode: listing $MEDIA_TAR instead of extracting it"
    tar -tzf "$MEDIA_TAR" | head -n 20
  fi
else
  # ---- Live: replace production data ----------------------------------------
  log "stopping web, celery and celery-beat"
  compose stop web celery celery-beat
  if [[ -n "$DB_DUMP" ]]; then
    log "restoring $DB_DUMP into the production database (pg_restore --clean)"
    # --clean/--if-exists drop and recreate each object; --no-owner/
    # --no-privileges because the dump's owner is the same single app role.
    compose exec -T db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' \
      < "$DB_DUMP" || status=$?
  fi
  if [[ -n "$MEDIA_TAR" ]]; then
    log "extracting $MEDIA_TAR over /app/media"
    # web is stopped; run the extraction in a one-off container on the same
    # image and volume, as appuser, so ownership matches what web expects.
    compose run --rm --no-deps -T web tar -C /app/media -xzf - < "$MEDIA_TAR" || status=$?
  fi
  log "starting the app again"
  compose up -d
fi

if [[ "$status" -ne 0 ]]; then
  log "finished with errors (exit $status) -- read the pg_restore/tar output above"
  exit "$status"
fi
log "finished cleanly"
