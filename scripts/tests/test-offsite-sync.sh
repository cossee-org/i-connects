#!/usr/bin/env bash
# Tests for scripts/offsite-sync.sh against a local rclone remote wrapped in
# the same crypt layer production uses. No network, no Google account.
#
#   scripts/tests/test-offsite-sync.sh
#
# Needs rclone (1.60+) and flock. Run by CI (.github/workflows/ci.yml).
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/offsite-sync.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

export RCLONE_CONFIG="$T/rclone.conf"
STORE="$T/store"            # what "Drive" holds: ciphertext, names + .bin
BK="$T/backups"
ENV="$T/offsite.env"
mkdir -p "$STORE" "$BK"
cat > "$RCLONE_CONFIG" <<EOF
[test-crypt]
type = crypt
remote = $STORE
filename_encryption = off
directory_name_encryption = false
password = $(rclone obscure test-password-one)
password2 = $(rclone obscure test-password-two)
EOF

write_env() {
  cat > "$ENV" <<EOF
OFFSITE_REMOTE=test-crypt:host
OFFSITE_NAME=test
BACKUP_DIR=$BK
OFFSITE_RETENTION_DAYS=90
OFFSITE_KEEP_MIN=3
$*
EOF
}

pass=0
ok()   { pass=$((pass + 1)); echo "ok   - $*"; }
die()  { echo "FAIL - $*" >&2; [[ -f "$T/out" ]] && sed 's/^/     | /' "$T/out" >&2; exit 1; }
run()  { "$SCRIPT" --env "$ENV" "$@" > "$T/out" 2>&1; }
remote_ls() { rclone lsf --files-only test-crypt:host 2>/dev/null | sort; }
stamp() { date -d "$1" +%Y-%m-%dT%H%M%S; }
mk() { head -c "${2:-2048}" /dev/urandom > "$BK/$1"; }

# --- Missing settings fail clearly ---------------------------------------------
write_env
sed -i '/^OFFSITE_REMOTE=/d' "$ENV"
run && die "ran without OFFSITE_REMOTE"
grep -q 'OFFSITE_REMOTE is not set' "$T/out" || die "no clear message for a missing OFFSITE_REMOTE"
ok "refuses to run without OFFSITE_REMOTE"

write_env
S1="$(stamp '-1 day')"; S2="$(stamp 'now')"
mk "db-$S1.dump"; mk "media-$S1.tar.gz"; mk "db-$S2.dump"; mk "media-$S2.tar.gz"
mk "db-$S2.dump.tmp"; echo "log line" > "$BK/backup.log"; echo x > "$BK/.hidden"

# --- Dry run writes nothing ------------------------------------------------------
run --dry-run || die "dry run failed"
[[ -z "$(ls -A "$STORE")" ]] || die "dry run wrote to the remote"
[[ ! -e "$BK/.test-last-ok" ]] || die "dry run touched the marker"
grep -q "would upload: db-$S2.dump" "$T/out" || die "dry run did not list planned uploads"
ok "--dry-run lists uploads and writes nothing"

# --- First run uploads finished backups only, encrypted ---------------------------
run || die "first sync failed"
[[ "$(remote_ls)" == "$(printf '%s\n' "db-$S1.dump" "db-$S2.dump" "media-$S1.tar.gz" "media-$S2.tar.gz" | sort)" ]] \
  || die "remote holds the wrong files: $(remote_ls | tr '\n' ' ')"
ok "uploads db dumps and media tarballs, skips .tmp, logs and dotfiles"
[[ -f "$STORE/host/db-$S2.dump.bin" ]] || die "expected crypt's .bin name on the underlying store"
cmp -s "$STORE/host/db-$S2.dump.bin" "$BK/db-$S2.dump" && die "remote copy is not encrypted"
ok "the stored copy is ciphertext"
[[ -f "$BK/.test-last-ok" ]] || die "marker not touched"
grep -q 'verifying 4 uploaded files (cryptcheck)' "$T/out" || die "uploads were not verified"
ok "verifies uploads and touches the success marker"

# --- Second run uploads nothing --------------------------------------------------
before="$(ls -l --time-style=+%s%N "$STORE/host")"
run || die "second sync failed"
grep -q 'nothing new to upload' "$T/out" || die "second run did not report nothing new"
[[ "$(ls -l --time-style=+%s%N "$STORE/host")" == "$before" ]] || die "second run rewrote remote files"
ok "a second run uploads nothing"

# --- Local deletions never reach the remote -----------------------------------------
mv "$BK/db-$S1.dump" "$T/aside.dump"
run || die "sync after a local delete failed"
remote_ls | grep -qx "db-$S1.dump" || die "a local delete removed the remote copy"
mv "$T/aside.dump" "$BK/db-$S1.dump"
ok "copy only: deleting a local backup keeps the remote copy"

# --- A remote file that differs is an error, not overwritten --------------------------
cp "$STORE/host/media-$S1.tar.gz.bin" "$T/orig.bin"
head -c 100 /dev/urandom >> "$STORE/host/media-$S1.tar.gz.bin"
run && die "sync succeeded although a remote file differs"
grep -qi 'immutable' "$T/out" || die "no immutable error reported"
cmp -s "$T/orig.bin" "$STORE/host/media-$S1.tar.gz.bin" && die "the differing remote file was replaced"
cp "$T/orig.bin" "$STORE/host/media-$S1.tar.gz.bin"
ok "--immutable: a differing remote file fails the run and is left alone"

# --- Download verification (the SMB/RDS mode) ------------------------------------------
write_env OFFSITE_VERIFY=download
S3="$(stamp '-2 days')"; mk "db-$S3.dump"
run || die "sync with OFFSITE_VERIFY=download failed"
grep -q 'verifying 1 uploaded files (download)' "$T/out" || die "download verification did not run"
ok "OFFSITE_VERIFY=download verifies by reading the copy back"

# --- Prune by the date in the name ---------------------------------------------------------
write_env
mkdir -p "$T/old"
O1="$(stamp '-200 days')"; O2="$(stamp '-120 days')"
for n in "db-$O1.dump" "media-$O1.tar.gz" "db-$O2.dump" "db-2020-01-01.sql.gz" "notes.txt"; do
  head -c 512 /dev/urandom > "$T/old/$n"
done
rclone copy "$T/old" test-crypt:host
run --no-prune || die "sync with --no-prune failed"
remote_ls | grep -qx "db-$O1.dump" || die "--no-prune pruned"
ok "--no-prune keeps old copies"
write_env OFFSITE_PRUNE=no
run || die "sync with OFFSITE_PRUNE=no failed"
remote_ls | grep -qx "db-$O1.dump" || die "OFFSITE_PRUNE=no pruned"
ok "OFFSITE_PRUNE=no keeps old copies"

write_env
run --prune-only --dry-run || die "prune dry run failed"
remote_ls | grep -qx "db-$O1.dump" || die "prune dry run deleted"
grep -q "would delete db-$O1.dump" "$T/out" || die "prune dry run did not list deletions"
ok "--prune-only --dry-run lists deletions and deletes nothing"

run || die "sync with prune failed"
for n in "db-$O1.dump" "media-$O1.tar.gz" "db-$O2.dump" "db-2020-01-01.sql.gz"; do
  remote_ls | grep -qx "$n" && die "old file $n was not pruned"
done
remote_ls | grep -qx notes.txt || die "a file that is not a backup was pruned"
remote_ls | grep -qx "db-$S2.dump" || die "a recent backup was pruned"
ok "prunes backups older than the retention by name, keeps recent and foreign files"

# --- Keep-minimum guard -----------------------------------------------------------------------
write_env OFFSITE_KEEP_MIN=10
rclone copy "$T/old" test-crypt:host
run || die "sync under the keep-min guard failed"
grep -q 'prune skipped (keep-min guard)' "$T/out" || die "keep-min guard did not report"
remote_ls | grep -qx "db-$O1.dump" || die "keep-min guard let the prune delete"
ok "keep-min guard: too few recent dumps means nothing is pruned"

# --- A failed verification stops before the prune and the marker ------------------------------
write_env
mkdir -p "$T/fakebin"
REAL_RCLONE="$(command -v rclone)"
# shellcheck disable=SC2016  # $1 and $@ belong to the fake rclone, not to this script
printf '#!/bin/sh\n[ "$1" = cryptcheck ] && { echo "fake: hash differs" >&2; exit 1; }\nexec %s "$@"\n' "$REAL_RCLONE" > "$T/fakebin/rclone"
chmod +x "$T/fakebin/rclone"
S4="$(stamp '-3 days')"; mk "db-$S4.dump"
touch -d '2000-01-01' "$BK/.test-last-ok"
PATH="$T/fakebin:$PATH" run && die "sync succeeded although verification failed"
grep -q 'verification failed' "$T/out" || die "no verification failure reported"
remote_ls | grep -qx "db-$O1.dump" || die "pruned after a failed verification"
[[ "$(date -r "$BK/.test-last-ok" +%Y)" == 2000 ]] || die "marker touched after a failed verification"
ok "a failed verification exits non-zero, does not prune and does not touch the marker"
[[ "$(cat "$BK/.test-unverified")" == "db-$S4.dump" ]] || die "the unverified upload was not recorded"
run || die "sync after a failed verification failed"
grep -q 'verifying 1 uploaded files' "$T/out" || die "the next run did not re-verify the failed upload"
[[ ! -e "$BK/.test-unverified" ]] || die "unverified list kept after a good verification"
ok "the next run re-verifies a file whose verification failed, then forgets it"

# --- Only one run at a time ---------------------------------------------------------------------
write_env
exec 8> "$BK/.test.lock"
flock -n 8
run && die "ran while another run held the lock"
grep -q 'holds' "$T/out" || die "no lock message"
exec 8>&-
ok "a second concurrent run refuses to start"

# --- List ---------------------------------------------------------------------------------------
run --list || die "--list failed"
grep -q "db-$S2.dump" "$T/out" || die "--list did not show a backup"
ok "--list shows the remote's files"

# --- Fetch for a restore -------------------------------------------------------------------------
mkdir "$T/restore"
run --fetch "db-$S2.dump" "media-$S2.tar.gz" --to "$T/restore" || die "--fetch failed"
cmp -s "$T/restore/db-$S2.dump" "$BK/db-$S2.dump" || die "fetched dump differs from the original"
cmp -s "$T/restore/media-$S2.tar.gz" "$BK/media-$S2.tar.gz" || die "fetched media differs from the original"
ok "--fetch downloads and decrypts named backups"
run --fetch "db-$S2.dump" --to "$T/restore" && die "--fetch overwrote an existing file"
ok "--fetch refuses to overwrite a local file"
run --fetch "db-1999-01-01T000000.dump" --to "$T/restore" && die "--fetch of a missing file succeeded"
ok "--fetch of a name that is not on the remote fails"

echo "all $pass offsite-sync tests passed"
