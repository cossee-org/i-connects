#!/usr/bin/env bash
# Copy scripts/backup.sh output to off-site storage with rclone, encrypted
# on this host before it leaves (GDRIVE_BACKUP.md).
#
#   scripts/offsite-sync.sh [--env FILE] [--dry-run] [--no-prune]
#   scripts/offsite-sync.sh [--env FILE] --prune-only [--dry-run]
#   scripts/offsite-sync.sh [--env FILE] --list
#   scripts/offsite-sync.sh [--env FILE] --fetch NAME... [--to DIR]
#
# Nightly, chained after the backup (DEPLOYMENT.md Part K):
#
#   0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1 && scripts/offsite-sync.sh --env ~/.config/i-connects/offsite-gdrive.env >> ~/backups/offsite-gdrive.log 2>&1
#
# A sync uploads every finished backup in BACKUP_DIR that the remote lacks
# (copy only: local deletions never reach the remote, and a remote file that
# differs from the local one is an error, never overwritten), verifies what
# it uploaded, prunes remote copies older than OFFSITE_RETENTION_DAYS by the
# date in their names, then touches BACKUP_DIR/.<OFFSITE_NAME>-last-ok and
# pings OFFSITE_PING_URL. Files that fail verification are listed in
# BACKUP_DIR/.<OFFSITE_NAME>-unverified and checked again on the next run.
# On a Google shared drive, pruned copies go to the drive's trash.
# --prune-only runs just the prune (e.g. after lowering the retention);
# --list prints what the remote holds; --fetch downloads named backups for
# scripts/restore.sh.
#
# Settings come from the --env file (default ~/.config/i-connects/offsite.env):
#   OFFSITE_REMOTE          rclone destination, e.g. gdrive-crypt:cossee-web-server (required)
#   OFFSITE_NAME            name for the lock/marker files        (default: offsite)
#   BACKUP_DIR              local source                          (default: ~/backups)
#   OFFSITE_RETENTION_DAYS  prune remote copies older than N days (default: 90)
#   OFFSITE_KEEP_MIN        never prune below N db dumps          (default: 14)
#   OFFSITE_PRUNE           "no" skips the prune in a sync        (default: yes)
#   OFFSITE_VERIFY          cryptcheck (remote has hashes, e.g. Drive) or
#                           download (no hashes, e.g. SMB)        (default: cryptcheck)
#   OFFSITE_BWLIMIT         rclone --bwlimit value                (default: unset)
#   OFFSITE_PING_URL        URL to GET after a successful sync    (default: unset)
# RCLONE_CONFIG picks the rclone.conf, as for rclone itself.
set -euo pipefail

ENV_FILE="$HOME/.config/i-connects/offsite.env"
MODE=sync
DRY_RUN=0
NO_PRUNE=0
FETCH_TO=""
FETCH_NAMES=()

usage() {
  sed -n '2,37p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)        ENV_FILE="${2:-}"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --no-prune)   NO_PRUNE=1; shift ;;
    --prune-only) MODE=prune; shift ;;
    --list)       MODE=list; shift ;;
    --fetch)      MODE=fetch; shift
                  while [[ $# -gt 0 && "$1" != --* ]]; do FETCH_NAMES+=("$1"); shift; done ;;
    --to)         FETCH_TO="${2:-}"; shift 2 ;;
    -h|--help)    usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

[[ -r "$ENV_FILE" ]] || { echo "cannot read settings file $ENV_FILE (see GDRIVE_BACKUP.md)" >&2; exit 1; }
set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a

OFFSITE_NAME="${OFFSITE_NAME:-offsite}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
OFFSITE_RETENTION_DAYS="${OFFSITE_RETENTION_DAYS:-90}"
OFFSITE_KEEP_MIN="${OFFSITE_KEEP_MIN:-14}"
OFFSITE_PRUNE="${OFFSITE_PRUNE:-yes}"
OFFSITE_VERIFY="${OFFSITE_VERIFY:-cryptcheck}"

log() { printf '%s offsite-sync[%s]: %s\n' "$(date -Is)" "$OFFSITE_NAME" "$*"; }

fail() { log "FAILED: $*" >&2; exit 1; }

# Anything set -e stops on without a fail() still leaves a FAILED line in the log.
trap 'log "FAILED: unexpected error on line $LINENO" >&2' ERR

# --- Preflight ----------------------------------------------------------------
[[ -n "${OFFSITE_REMOTE:-}" ]] || fail "OFFSITE_REMOTE is not set in $ENV_FILE"
[[ "$OFFSITE_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || fail "OFFSITE_NAME must be letters, digits, - or _"
[[ "$OFFSITE_RETENTION_DAYS" =~ ^[0-9]+$ ]] || fail "OFFSITE_RETENTION_DAYS must be a whole number"
[[ "$OFFSITE_KEEP_MIN" =~ ^[0-9]+$ ]] || fail "OFFSITE_KEEP_MIN must be a whole number"
[[ "$OFFSITE_VERIFY" == cryptcheck || "$OFFSITE_VERIFY" == download ]] \
  || fail "OFFSITE_VERIFY must be cryptcheck or download"
command -v rclone > /dev/null || fail "rclone is not installed (sudo apt install rclone)"
rclone_version="$(rclone version | sed -n '1s/^rclone v\([0-9]*\)\.\([0-9]*\).*/\1 \2/p')"
read -r rc_major rc_minor <<< "${rclone_version:-0 0}"
(( rc_major > 1 || (rc_major == 1 && rc_minor >= 60) )) \
  || fail "rclone 1.60 or later is needed, found: $(rclone version | head -1)"

REMOTE="${OFFSITE_REMOTE%/}"
RCLONE_FLAGS=(--stats 0)
[[ -n "${OFFSITE_BWLIMIT:-}" ]] && RCLONE_FLAGS+=(--bwlimit "$OFFSITE_BWLIMIT")

mkdir -p "$BACKUP_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# One run at a time per destination; a slow upload must not overlap the next.
exec 9> "$BACKUP_DIR/.$OFFSITE_NAME.lock"
flock -n 9 || fail "another $OFFSITE_NAME run holds $BACKUP_DIR/.$OFFSITE_NAME.lock"

# Names of the files on the remote, one per line, sorted. A folder that does
# not exist yet lists as empty (rclone exit 3).
remote_names() {
  local rc=0
  rclone lsf --files-only "${RCLONE_FLAGS[@]}" "$REMOTE" > "$WORK/remote.raw" 2> "$WORK/lsf.err" || rc=$?
  if [[ $rc -eq 3 ]]; then
    : > "$WORK/remote.raw"
  elif [[ $rc -ne 0 ]]; then
    cat "$WORK/lsf.err" >&2
    fail "cannot list $REMOTE (rclone exit $rc)"
  fi
  sort "$WORK/remote.raw"
}

# Drive allows two files with one name; which copy restores is then a guess.
check_duplicates() {
  local dups
  dups="$(uniq -d < "$1")"
  [[ -z "$dups" ]] || fail "duplicate names on $REMOTE, investigate before anything else: $(echo "$dups" | tr '\n' ' ')"
}

# The date part of a backup name as YYYY-MM-DD, or nothing if the name is not
# one of ours (such files are never pruned).
name_date() {
  if [[ "$1" =~ ^(db|media)-([0-9]{4}-[0-9]{2}-[0-9]{2})T[0-9]{6}\.(dump|tar\.gz)$ ]]; then
    echo "${BASH_REMATCH[2]}"
  elif [[ "$1" =~ ^db-([0-9]{4}-[0-9]{2}-[0-9]{2})\.sql\.gz$ ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

is_db_dump() { [[ "$1" == db-*.dump || "$1" == db-*.sql.gz ]]; }

prune() {
  local cutoff name d keep=0 old=0
  cutoff="$(date -d "-$OFFSITE_RETENTION_DAYS days" +%Y-%m-%d)"
  remote_names > "$WORK/prune.list"
  check_duplicates "$WORK/prune.list"
  : > "$WORK/prune.delete"
  while IFS= read -r name; do
    d="$(name_date "$name")"
    [[ -n "$d" ]] || continue
    if [[ "$d" < "$cutoff" ]]; then
      echo "$name" >> "$WORK/prune.delete"; old=$((old + 1))
    elif is_db_dump "$name"; then
      keep=$((keep + 1))
    fi
  done < "$WORK/prune.list"

  if [[ $old -eq 0 ]]; then
    log "prune: nothing older than $OFFSITE_RETENTION_DAYS days ($cutoff)"
    return
  fi
  if [[ $keep -lt $OFFSITE_KEEP_MIN ]]; then
    log "prune skipped (keep-min guard): only $keep db dumps newer than $cutoff, OFFSITE_KEEP_MIN is $OFFSITE_KEEP_MIN; $old old files kept"
    return
  fi
  log "prune: removing $old files older than $cutoff ($keep db dumps remain)"
  while IFS= read -r name; do
    if [[ $DRY_RUN -eq 1 ]]; then
      log "prune (dry run): would delete $name"
    else
      rclone deletefile "${RCLONE_FLAGS[@]}" "$REMOTE/$name" || fail "could not delete $name"
      log "prune: deleted $name"
    fi
  done < "$WORK/prune.delete"
}

case "$MODE" in
# --- List -----------------------------------------------------------------------
list)
  rc=0
  rclone lsl "${RCLONE_FLAGS[@]}" "$REMOTE" | sort -k4 || rc=$?
  [[ $rc -eq 0 ]] || fail "cannot list $REMOTE (rclone exit $rc)"
  remote_names > "$WORK/list"
  check_duplicates "$WORK/list"
  log "$(wc -l < "$WORK/list") files on $REMOTE, $(grep -cE '^db-.*\.(dump|sql\.gz)$' "$WORK/list" || true) of them db dumps"
  ;;

# --- Fetch ------------------------------------------------------------------------
fetch)
  [[ ${#FETCH_NAMES[@]} -gt 0 ]] || fail "--fetch needs at least one file name (see --list)"
  dest="${FETCH_TO:-$BACKUP_DIR}"
  mkdir -p "$dest"
  printf '%s\n' "${FETCH_NAMES[@]}" > "$WORK/fetch"
  for name in "${FETCH_NAMES[@]}"; do
    [[ "$name" != */* ]] || fail "--fetch takes bare file names, not paths: $name"
    [[ ! -e "$dest/$name" ]] || fail "$dest/$name already exists; move it aside or use --to"
  done
  log "fetching ${#FETCH_NAMES[@]} files from $REMOTE into $dest"
  rclone copy "${RCLONE_FLAGS[@]}" --files-from "$WORK/fetch" "$REMOTE" "$dest" \
    || fail "download failed"
  for name in "${FETCH_NAMES[@]}"; do
    [[ -s "$dest/$name" ]] || fail "$name was not downloaded (is it on the remote? see --list)"
  done
  log "done: $(cd "$dest" && du -ch "${FETCH_NAMES[@]}" | tail -1 | cut -f1) in $dest"
  ;;

# --- Prune only -------------------------------------------------------------------
prune)
  prune
  log "done"
  ;;

# --- Sync -------------------------------------------------------------------------
sync)
  # Finished backups only: backup.sh writes *.tmp and renames when done, and
  # logs and dotfiles are never uploaded.
  find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'db-*.dump' -o -name 'media-*.tar.gz' -o -name 'db-*.sql.gz' \) \
    -printf '%f\n' | sort > "$WORK/local"
  [[ -s "$WORK/local" ]] || fail "no backups in $BACKUP_DIR to copy"

  if [[ $DRY_RUN -eq 0 ]]; then
    rclone mkdir "${RCLONE_FLAGS[@]}" "$REMOTE" || fail "cannot create $REMOTE"
  fi
  remote_names > "$WORK/before"
  check_duplicates "$WORK/before"
  comm -23 "$WORK/local" "$WORK/before" > "$WORK/new"
  log "$(wc -l < "$WORK/local") local backups, $(wc -l < "$WORK/before") on the remote, $(wc -l < "$WORK/new") to upload"

  # Every local backup goes through copy, so one that already exists on the
  # remote with a different size fails --immutable instead of being skipped.
  # Sizes, not times: SMB and Drive keep modification times differently, and
  # content is checked below by hash or by download.
  copy_flags=(--immutable --size-only)
  [[ $DRY_RUN -eq 1 ]] && copy_flags+=(--dry-run)
  rclone copy "${RCLONE_FLAGS[@]}" "${copy_flags[@]}" --files-from "$WORK/local" "$BACKUP_DIR" "$REMOTE" \
    || fail "upload failed (a file on the remote that differs from the local one is reported above as immutable)"

  if [[ $DRY_RUN -eq 1 ]]; then
    sed 's/^/would upload: /' "$WORK/new"
    [[ $NO_PRUNE -eq 1 || "$OFFSITE_PRUNE" == no ]] || prune
    log "dry run: nothing was written"
    exit 0
  fi

  # Verify this run's uploads plus any a previous run could not verify, so a
  # bad copy is never taken for a good one just because it is already there.
  pending="$BACKUP_DIR/.$OFFSITE_NAME-unverified"
  cp "$WORK/new" "$WORK/verify"
  if [[ -f "$pending" ]]; then
    comm -12 "$WORK/local" <(sort "$pending") >> "$WORK/verify"
    sort -u -o "$WORK/verify" "$WORK/verify"
  fi
  [[ -s "$WORK/new" ]] || log "nothing new to upload"
  if [[ -s "$WORK/verify" ]]; then
    log "verifying $(wc -l < "$WORK/verify") uploaded files ($OFFSITE_VERIFY)"
    check_cmd=(cryptcheck)
    [[ "$OFFSITE_VERIFY" == download ]] && check_cmd=(check --download)
    if ! rclone "${check_cmd[@]}" "${RCLONE_FLAGS[@]}" --one-way --files-from "$WORK/verify" "$BACKUP_DIR" "$REMOTE"; then
      cp "$WORK/verify" "$pending"
      fail "verification failed: an uploaded file does not match the local copy; not pruning (listed in $pending, re-checked next run)"
    fi
  fi
  rm -f "$pending"

  if [[ $NO_PRUNE -eq 1 || "$OFFSITE_PRUNE" == no ]]; then
    log "prune: skipped (--no-prune / OFFSITE_PRUNE=no)"
  else
    prune
  fi

  touch "$BACKUP_DIR/.$OFFSITE_NAME-last-ok"
  if [[ -n "${OFFSITE_PING_URL:-}" ]]; then
    curl -fsS -m 10 --retry 3 -o /dev/null "$OFFSITE_PING_URL" \
      || log "warning: sync succeeded but the ping to OFFSITE_PING_URL failed"
  fi
  log "done: uploaded $(wc -l < "$WORK/new") files"
  ;;
esac
