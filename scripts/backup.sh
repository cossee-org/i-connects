#!/usr/bin/env bash
# Back up the i-connects stack: a Postgres custom-format dump plus a tarball
# of the media volume, with retention pruning.
#
# Run from the compose project directory (the repo checkout) on the host:
#
#   scripts/backup.sh
#
# Nightly cron (DEPLOYMENT.md Part K):
#
#   0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1
#
# Environment overrides:
#   BACKUP_DIR      where to write               (default: ~/backups)
#   RETENTION_DAYS  delete backups older than N  (default: 30)
#   COMPOSE_FILES   `docker compose` -f flags    (default: dev + prod files)
#
# Restore with scripts/restore.sh.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.prod.yml}"
STAMP="$(date +%Y-%m-%dT%H%M%S)"

compose() {
  # shellcheck disable=SC2086  # COMPOSE_FILES is a list of flags on purpose
  docker compose $COMPOSE_FILES "$@"
}

log() { printf '%s backup: %s\n' "$(date -Is)" "$*"; }

fail() { log "FAILED: $*" >&2; exit 1; }

mkdir -p "$BACKUP_DIR"

# --- Database ---------------------------------------------------------------
# pg_dump runs inside the db container with that container's own POSTGRES_*
# environment, so no credentials are read from .env here. Custom format
# (-Fc) is compressed and restores selectively with pg_restore.
db_out="$BACKUP_DIR/db-$STAMP.dump"
log "dumping database to $db_out"
compose exec -T db sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$db_out.tmp" \
  || fail "pg_dump exited non-zero"
[[ -s "$db_out.tmp" ]] || fail "database dump is empty"
# Integrity: the archive's table of contents must be readable.
compose exec -T db pg_restore --list < "$db_out.tmp" > /dev/null \
  || fail "pg_restore cannot read the dump it was just given"
mv "$db_out.tmp" "$db_out"

# --- Media ------------------------------------------------------------------
# /app/media is the media_data volume, mounted in web (and celery). A one-off
# container on the web image reads it, so the backup works even while web is
# down or restarting; tar runs as the image's appuser, which owns the files.
media_out="$BACKUP_DIR/media-$STAMP.tar.gz"
log "archiving media volume to $media_out"
compose run --rm --no-deps -T web tar -C /app/media -czf - . > "$media_out.tmp" \
  || fail "tar of /app/media exited non-zero"
gzip -t "$media_out.tmp" || fail "media archive is corrupt"
mv "$media_out.tmp" "$media_out"

# --- Retention ----------------------------------------------------------------
log "pruning backups older than $RETENTION_DAYS days"
find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name 'db-*.dump' -o -name 'media-*.tar.gz' \) \
  -mtime +"$RETENTION_DAYS" -print -delete

log "done: database $(du -h "$db_out" | cut -f1), media $(du -h "$media_out" | cut -f1)"
