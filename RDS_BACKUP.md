# Off-site Backups to UAlberta Research Data Storage — plan

**Status: plan only, nothing here is implemented yet.** This closes the gap
named in `DEPLOYMENT.md` Part K ("Off-host copy"): the nightly
`scripts/backup.sh` writes to `~/backups` on `cossee-web-server`, and if that
host is lost, the backups go with it. This plan copies every new backup
to the University of Alberta **Research Data Storage (RDS)** service
([service page](https://www.ualberta.ca/en/information-services-and-technology/initiatives/research-data-storage-service.html))
right after the nightly backup finishes.

Contents:

1. [What it does](#1-what-it-does)
2. [Before any code: things to confirm with IST](#2-before-any-code-things-to-confirm-with-ist)
3. [Design](#3-design)
4. [Implementation steps](#4-implementation-steps)
5. [Operator instructions (after implementation)](#5-operator-instructions-after-implementation)
6. [Restore from RDS](#6-restore-from-rds)
7. [Monitoring and failure modes](#7-monitoring-and-failure-modes)
8. [Acceptance checklist](#8-acceptance-checklist)
9. [Changes to other docs](#9-changes-to-other-docs)

---

## 1. What it does

1. At 03:00 cron runs `scripts/backup.sh`, which writes
   `~/backups/db-<stamp>.dump` and `~/backups/media-<stamp>.tar.gz` and
   checks that both are readable. This is how it works today.
2. **New:** if the backup succeeded, cron runs `scripts/offsite-sync.sh`,
   which:
   - uploads every finished backup file in `~/backups` that RDS doesn't
     have yet, **encrypted on the host before it leaves**;
   - checks that each uploaded file matches the local copy;
   - removes RDS copies older than the off-site retention period (default
     90 days). It never deletes the newest copies, and it only prunes after
     a successful upload;
   - on success, updates a marker file and (optionally) pings a monitoring
     URL, so that a job that stops working gets noticed.
3. The job is **copy-only**: deleting or corrupting files in `~/backups`
   does not change what is on RDS. Old RDS copies are removed only by the
   prune step, which works from the file's age.

Two copies, two retention periods:

| Where | Written by | Retention | Protects against |
| --- | --- | --- | --- |
| `~/backups` on `cossee-web-server` | `scripts/backup.sh` | 30 days (`RETENTION_DAYS`) | bad deploy or migration, accidental deletes; the fastest restore |
| RDS | `scripts/offsite-sync.sh` | 90 days (`OFFSITE_RETENTION_DAYS`) | losing the VM or its disk, host compromise, mistakes noticed late |

---

## 2. Before any code: things to confirm with IST

I couldn't load the RDS service page from the environment where this plan
was written, so the details below are **unconfirmed**. Get answers from IST
(or the RDS documentation) first. Several answers change the configuration,
but the design in §3 works whatever the answers are.

| # | Question | Why it matters | Where the answer goes |
| --- | --- | --- | --- |
| Q1 | **How do hosts reach RDS?** SMB/CIFS, NFS mount, SFTP/SSH, Globus, or S3-compatible object storage? Which ones work from a Linux server with no one logged in? | Decides which rclone backend to use (§3.2). | `rclone.conf` remote type |
| Q2 | **Can we get a service or non-person account** that works without Duo/MFA prompts (SSH key, S3 access key, or Kerberos keytab)? | A job that runs at 03:00 can't answer an MFA prompt. Using a person's CCID also means backups stop when that person leaves. | Credentials in `rclone.conf` |
| Q3 | **Which data classification is RDS approved for?** The database dump holds personal information: names, emails, password hashes, session data. | Must be allowed for this data. Client-side encryption (§3.3) is planned either way, but it doesn't replace approval. | Sign-off recorded in §8 |
| Q4 | **Who owns the allocation, and what are the quota and cost?** Is it tied to a PI or project? | Sizing: today about 30 nightly dumps plus media. Expect a few GB at most. Check with `du -sh ~/backups`. | Plan retention with quota in mind |
| Q5 | **Can `cossee-web-server` reach RDS over the network?** (The host is `142.244.87.193`, on the UAlberta network.) Are ports or subnets allow-listed? | Firewalls on either side can block the connection. | UFW egress, if restricted (Part B4) |
| Q6 | **Does RDS keep its own snapshots or versions**, and for how long? | If it does, those snapshots also protect against a compromised host overwriting files on RDS. If it doesn't, the append-only rules in §3.4 matter more. | §3.4 |
| Q7 | **Hostname, share or bucket name, and path** for the allocation. | Configuration values. | `OFFSITE_REMOTE` |

**Put the answers in this file** (a short "Confirmed with IST on
<date>" table) before starting §4. That turns this into the record of how
the off-site copy works.

---

## 3. Design

### 3.1 Tool: `rclone`

Use [`rclone`](https://rclone.org) (`sudo apt install rclone`, or the
static binary). Reasons:

- **One tool for every possible Q1 answer.** SFTP, SMB, S3, WebDAV and a
  mounted path (NFS/SMB mounted by IST) are all rclone "remotes". Changing
  protocol means editing `rclone.conf`, not the script.
- **Built-in client-side encryption** (a `crypt` remote wrapped around the
  storage remote). See §3.3.
- **`rclone copy`, `rclone check` and `rclone delete --min-age`** cover
  upload, verification and retention directly.

Fallback: if RDS turns out to be a plain SSH host and rclone isn't
acceptable, `rsync -a --ignore-existing` over SSH with a GPG-encrypted
staging step does the same job with more moving parts. rclone is the
default.

### 3.2 Configuration

Everything off-site is configured under `~/.config/i-connects/`, owned by
`deploy`, mode `0600`. **Nothing goes in the app's `.env`.** The app never
needs these values, and `backup.sh` already reads nothing from `.env`.

```
~/.config/rclone/rclone.conf        # remotes: "rds" (storage) and "rds-crypt" (encryption wrapper)
~/.config/i-connects/offsite.env    # settings for offsite-sync.sh (below)
```

`offsite.env` variables (script defaults in brackets):

| Variable | Meaning |
| --- | --- |
| `OFFSITE_REMOTE` | rclone destination, e.g. `rds-crypt:i-connects/cossee-web-server` **(required, no default)** |
| `BACKUP_DIR` | local source [`~/backups`, same as `backup.sh`] |
| `OFFSITE_RETENTION_DAYS` | delete RDS copies older than this [`90`] |
| `OFFSITE_KEEP_MIN` | never prune below this many db dumps [`14`] |
| `OFFSITE_BWLIMIT` | optional rclone `--bwlimit`, so the job doesn't saturate the shared host's link [unset] |
| `OFFSITE_PING_URL` | optional URL to `curl` on success (Healthchecks.io-style dead-man's switch) [unset] |

Example `rclone.conf` shapes. Use the one that matches Q1; the values are
placeholders.

```ini
# If RDS offers SFTP with a key-based service account:
[rds]
type = sftp
host = <rds-host-from-IST>
user = <service-account>
key_file = ~/.ssh/rds_backup_ed25519

# If IST mounts the allocation on the host (NFS/SMB) at /mnt/rds:
# [rds]
# type = alias
# remote = /mnt/rds/<allocation>

# If RDS is S3-compatible:
# [rds]
# type = s3
# provider = Other
# endpoint = <endpoint-from-IST>
# access_key_id = ...
# secret_access_key = ...

# The encryption wrapper. The script always writes through this one.
[rds-crypt]
type = crypt
remote = rds:<allocation-path>/i-connects-backups
filename_encryption = off        # keep db-<stamp>.dump names readable, so restores and pruning are simple
directory_name_encryption = false
password = <rclone obscure output>
password2 = <rclone obscure output>
```

### 3.3 Encryption and key custody

- Files are encrypted by rclone `crypt` **on the host, before upload**.
  RDS only ever holds ciphertext, so an RDS-side leak doesn't expose user
  data. This holds whatever the Q3 answer is.
- File names stay readable (`filename_encryption = off`). They contain
  only timestamps, and readable names keep the pruning and restore steps
  simple.
- **Store the crypt passwords outside the host**, in the same secret store
  as `.env` (Part K already says `.env` isn't in any backup on purpose). If
  the passwords are lost, **every off-site backup is unreadable**. Keeping
  them only on the VM defeats the point, because this backup exists for the
  case where the VM is gone.
- Treat a leak of the passwords the same as a `.env` leak (Part M): rotate
  them, then re-upload under the new key. `rclone` can't re-key files in
  place, so the next full run uploads everything again. Delete the old
  prefix once the new copies pass `rclone check`.

### 3.4 Safety rules the script must follow

1. **Never propagate deletions.** Use `rclone copy`, never `rclone sync`.
   With `sync`, an emptied or ransomwared `~/backups` would empty RDS on
   the next run.
2. **Don't overwrite on RDS.** Pass `--immutable`, so a file that already
   exists on RDS with different contents is reported as an error, not
   replaced. Backup files never change after `backup.sh` renames them into
   place, so any mismatch means something is wrong.
3. **Upload only finished files.** Filter to `db-*.dump`,
   `media-*.tar.gz` and the legacy `db-*.sql.gz`. Exclude `*.tmp`, because
   `backup.sh` writes to `.tmp` and renames when done. Also exclude
   `backup.log` and `offsite.log`. Logs can contain paths and hostnames and
   aren't needed for a restore.
4. **Verify after upload.** Run `rclone check` on the files uploaded in
   this run (use `cryptcheck` for the crypt remote, which compares checksums
   of the encrypted data). If it fails, exit non-zero and don't prune.
5. **Prune carefully.** Run `rclone delete --min-age
   ${OFFSITE_RETENTION_DAYS}d` only after steps 1–4 succeed, and only if
   at least `OFFSITE_KEEP_MIN` db dumps would remain. This keeps a run of
   failed backups from causing the prune step to delete the last good
   copies.
6. **Allow one run at a time.** Wrap the script in `flock` on
   `~/backups/.offsite.lock`, so a slow upload can't overlap the next
   night's run.
7. **Keep the same shape as `backup.sh`:** `set -euo pipefail`, the same
   `log()`/`fail()` format with timestamped lines, and exit non-zero on any
   failure so cron and monitoring can see it.

If IST confirms RDS keeps its own snapshots (Q6), those also protect
against someone who has compromised the host and deletes files on RDS. If
it doesn't, consider giving the service account write-without-delete
permission and running pruning from a separate account. Decide this once
Q2 and Q6 are answered.

### 3.5 Scheduling

Chain the new script after the existing backup in the same cron entry, so
the off-site copy happens right after each successful backup:

```cron
0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1 && scripts/offsite-sync.sh >> ~/backups/offsite.log 2>&1
```

- `&&` means a failed backup doesn't trigger an upload. That run's
  failure shows up in `backup.log` and in monitoring (§7).
- Missed runs catch up: the script uploads **every** local file RDS lacks,
  not just tonight's. If one night's upload fails, the next successful run
  uploads both nights' files. The local 30-day retention is the window for
  noticing and fixing a broken off-site job before backups start
  disappearing from the host without an off-site copy.

---

## 4. Implementation steps

Do these in order once §2 is answered. Each step can be its own PR.

1. **Record IST's answers** in §2 of this file.
2. **Add `scripts/offsite-sync.sh`** following §3.2–3.4:
   - load `~/.config/i-connects/offsite.env`; fail clearly if
     `OFFSITE_REMOTE` is unset or `rclone` is missing;
   - `flock` → `rclone copy --immutable` with the include/exclude filters →
     `rclone cryptcheck` → guarded `rclone delete --min-age` → touch
     `~/backups/.offsite-last-ok` → optional `curl -fsS "$OFFSITE_PING_URL"`;
   - support `--dry-run` (passed through to rclone) for first-time setup,
     and `--list` to print what RDS holds (for restores);
   - header comment in the same style as `backup.sh` (usage, cron line,
     environment overrides).
3. **Add `scripts/offsite-restore.sh`**, or a `--fetch <file>` mode on the
   sync script: download a named backup from RDS into `~/backups` (or a
   given directory) so `scripts/restore.sh` can use it unchanged (§6).
4. **CI:** `.github/workflows/ci.yml` already runs `bash -n scripts/*.sh`,
   which covers the new scripts automatically. Also consider adding
   `shellcheck` for `scripts/`. There's no RDS in CI, so the rclone logic
   can be tested against a `local` remote pointed at a temp directory:
   copy, run again to confirm it does nothing, tamper with a file to confirm
   `--immutable` fails, and add an old file to confirm the prune and the
   keep-minimum guard.
5. **Update docs** (§9).
6. **Deploy on `cossee-web-server`** using §5, then run the §8 checklist.

---

## 5. Operator instructions (after implementation)

Run on `cossee-web-server` as `deploy`, from `~/i-connects`.

### 5.1 One-time setup

```bash
sudo apt install rclone
mkdir -p ~/.config/i-connects && chmod 700 ~/.config/i-connects

# If Q1 = SFTP: create a dedicated key and send the public half to IST for the service account
ssh-keygen -t ed25519 -f ~/.ssh/rds_backup_ed25519 -N '' -C 'i-connects backups @ cossee-web-server'

rclone config                       # create "rds" (per Q1), then "rds-crypt" wrapping it (per §3.2)
chmod 600 ~/.config/rclone/rclone.conf
rclone lsd rds:                     # can we reach the allocation?

cat > ~/.config/i-connects/offsite.env <<'EOF'
OFFSITE_REMOTE=rds-crypt:i-connects/cossee-web-server
OFFSITE_RETENTION_DAYS=90
OFFSITE_KEEP_MIN=14
EOF
chmod 600 ~/.config/i-connects/offsite.env
```

**Now store the two crypt passwords (the plaintext you typed into
`rclone config`, not the obscured values) in the team secret store, next to
`.env`.** Don't skip this.

### 5.2 First run

```bash
scripts/offsite-sync.sh --dry-run   # lists what would be uploaded; nothing is written
scripts/offsite-sync.sh             # first real run uploads the whole ~/backups history
scripts/offsite-sync.sh             # second run should upload nothing
scripts/offsite-sync.sh --list      # the files RDS now holds
```

### 5.3 Wire into cron

`crontab -e` as `deploy`, and **replace** the existing Part K backup line
with the chained line from §3.5.

### 5.4 Prove it once: a restore from RDS

Do §6 with `--target` straight after setup. Until a file downloaded from
RDS has been restored successfully, don't count the off-site backup as
working.

---

## 6. Restore from RDS

For when the host is gone or `~/backups` is unusable. On a rebuilt host,
first follow `DEPLOYMENT.md` Parts B–H, then:

1. Install rclone, and recreate `rclone.conf` from the secret store: the
   RDS credentials plus the **same** crypt passwords.
2. Find and fetch the backup you want:
   ```bash
   scripts/offsite-sync.sh --list
   scripts/offsite-restore.sh db-<stamp>.dump media-<stamp>.tar.gz   # → ~/backups/
   ```
   (Without the script: `rclone copy rds-crypt:<path>/db-<stamp>.dump ~/backups/`.)
3. Restore with the existing tool, unchanged:
   ```bash
   scripts/restore.sh --db ~/backups/db-<stamp>.dump --media ~/backups/media-<stamp>.tar.gz --yes
   ```
4. Continue with Part I (post-launch verification).

**Quarterly drill** (extends the Part K drill): fetch the newest dump
**from RDS**, not from `~/backups`, into a scratch directory, and restore it
with `--target drill_$(date +%Y%m)`. This proves the network path, the
credentials and the encryption key all still work.

---

## 7. Monitoring and failure modes

- **Success marker:** `~/backups/.offsite-last-ok` is updated on every
  fully successful run. Check its age during routine checks
  (`stat -c %y ~/backups/.offsite-last-ok`); anything older than about 26
  hours means the job is failing.
- **Dead-man's switch (recommended):** set `OFFSITE_PING_URL` to a check
  that alerts when no ping arrives within 26 hours. This also catches a
  failed `backup.sh`, because the chained sync then doesn't run.
- **Logs:** `~/backups/offsite.log`, one timestamped line per step, in the
  same style as `backup.log`. Both files grow without limit. Add them to
  the log rotation in Part L, or have the scripts cap their size.

| Symptom in `offsite.log` | Likely cause | Action |
| --- | --- | --- |
| auth / permission denied | service-account key or password rotated or expired | Renew with IST, update `rclone.conf` |
| connection timed out | RDS maintenance, or a firewall change (Q5) | Retry next night; if it persists, contact IST. Local backups keep 30 days |
| quota exceeded | allocation full (Q4) | Lower `OFFSITE_RETENTION_DAYS`, or request more quota |
| `--immutable` / cryptcheck mismatch | a file on RDS differs from the local file of the same name | **Investigate before doing anything else**: possible corruption or tampering. Don't delete either copy |
| prune skipped (keep-min guard) | fewer than `OFFSITE_KEEP_MIN` dumps on RDS | Expected in the first two weeks; after that, backups have been failing, so check `backup.log` |

---

## 8. Acceptance checklist

Off-site backups are "done" (per Part K) only when every box is ticked:

- [ ] §2 answered and recorded; RDS approved for this data classification (Q3)
- [ ] Service account in use, not a personal CCID (Q2)
- [ ] Crypt passwords and RDS credentials stored in the team secret store, off the host
- [ ] Second run uploads nothing; `--list` shows the full `~/backups` history
- [ ] Cron line from §3.5 installed; the next morning `offsite.log` shows a clean run
- [ ] Monitoring alert set up and tested once (skip one ping, confirm the alert fires)
- [ ] One file restored from RDS with `restore.sh --target` and spot-checked (§5.4)
- [ ] Quarterly drill in §6 added to the team calendar

---

## 9. Changes to other docs

When this is implemented:

- **`DEPLOYMENT.md` Part K:** replace the "Off-host copy" paragraph with a
  short summary and a link here. Replace the cron line with the chained one
  from §3.5.
- **`DEPLOYMENT.md` Part L:** add `offsite.log` to log rotation and the
  staleness check to routine monitoring.
- **`DEPLOYMENT.md` Part M:** add "RDS credentials / crypt passwords" to
  the secret-rotation list.
- **`scripts/backup.sh` header:** mention that off-site copying is handled
  by `scripts/offsite-sync.sh`.
