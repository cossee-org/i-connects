# Off-site Backups to Google Drive

The nightly `scripts/backup.sh` writes to `~/backups` on
`cossee-web-server`, so if that host is lost, the backups go with it
(`DEPLOYMENT.md` Part K). `scripts/offsite-sync.sh` closes that gap: right
after each successful backup, it copies every new backup to a folder in a
**Google shared drive**, encrypted on the host before it leaves.

This file is both the documentation and the set-up guide:

- **§1–2** explain how it works and what to have in place first.
- **§3** is the one-time set-up on `cossee-web-server`, step by step.
- **§4–8** are the reference, restore, monitoring and routine tasks.
- **§9** is the checklist for calling off-site backups "done".

**Status:** the script, its tests and CI are in the repository. The set-up
in §3 has not been done on `cossee-web-server` yet. Fill in the set-up
record (§2.2) as you go.

Contents:

1. [How it works](#1-how-it-works)
2. [Before you start](#2-before-you-start)
3. [Set-up, step by step](#3-set-up-step-by-step)
4. [Reference: `scripts/offsite-sync.sh`](#4-reference-scriptsoffsite-syncsh)
5. [Restore from Google Drive](#5-restore-from-google-drive)
6. [Monitoring and troubleshooting](#6-monitoring-and-troubleshooting)
7. [Routine tasks](#7-routine-tasks)
8. [Running alongside RDS](#8-running-alongside-rds)
9. [Acceptance checklist](#9-acceptance-checklist)
10. [Design notes](#10-design-notes)

---

## 1. How it works

1. At 03:00 cron runs `scripts/backup.sh`, which writes
   `~/backups/db-<stamp>.dump` and `~/backups/media-<stamp>.tar.gz` and
   checks that both are readable.
2. If that succeeded, the same cron line runs `scripts/offsite-sync.sh`.
   It talks to the Google Drive API over HTTPS with
   [`rclone`](https://rclone.org), without mounting anything, and:
   1. takes a lock, so two runs never overlap;
   2. uploads every finished backup in `~/backups` that the shared drive
      doesn't have yet, **encrypted on the host** by rclone's `crypt`
      layer;
   3. verifies what it just uploaded against the checksums Drive stores
      (`rclone cryptcheck`), without downloading it again;
   4. deletes Drive copies older than 90 days, going by the date in each
      file name. It never prunes when fewer than 14 recent db dumps would
      remain, and never after a failed upload or check. Deleted copies go
      to the shared drive's trash, where they can be restored for 30
      days;
   5. touches `~/backups/.gdrive-last-ok` and, if configured, pings a
      monitoring URL.

**What it never does:**
- **Delete to match the host.** It only copies. Deleting or ransomwaring
  files in `~/backups` doesn't change what's on Drive.
- **Overwrite a copy.** If a file on Drive already exists with a different
  size from the local file, the run fails and reports it.
- **Upload anything but finished backups.** It uploads only `db-*.dump`,
  `media-*.tar.gz` and legacy `db-*.sql.gz`. It never uploads `*.tmp`
  files, logs or dotfiles.

Two copies, two retention periods:

| Where | Written by | Retention | Protects against |
| --- | --- | --- | --- |
| `~/backups` on `cossee-web-server` | `scripts/backup.sh` | 30 days (`RETENTION_DAYS`) | bad deploy or migration, accidental deletes; the fastest restore |
| Google shared drive, `i-connects-backups/cossee-web-server/` | `scripts/offsite-sync.sh` | 90 days (`OFFSITE_RETENTION_DAYS`) | losing the VM or its disk, host compromise, mistakes noticed late |

**Who can delete what.** The nightly job's account is a **Content
manager** on the shared drive, so each nightly run prunes by itself. A
Content manager can move files to the trash, but only a **Manager** can
delete them permanently or empty the trash. So the worst a compromised
host can do is move every copy to the trash. From there a Manager can
restore them for 30 days, after which Drive empties the trash. That's
why the nightly account must never be a Manager, and why someone should
look at the trash once a month (§7.1). Because the files belong to the
shared drive, not to a person, they stay when people leave.

Drive is not a backup of itself: a Manager's credential can empty the
trash. VM disk snapshots (Part K) are still worth having.

---

## 2. Before you start

### 2.1 Things to have in place

Get these from the shared drive's Manager or the Workspace admin. Record
each answer in §2.2 as you go.

| # | What you need | Why |
| --- | --- | --- |
| **P1** | **The nightly account.** In order of preference: (a) a functional Workspace account (not a person's), used for nothing but this job; (b) a service account, if the Workspace allows service-account keys and adding one to a shared drive; (c) a named person's account, with the risk written down. Don't reuse the Gmail account from Part F1. | With (c), the files are safe, but the job stops when that person leaves or is suspended, and a leaked token reaches whatever else they can open. Reusing the SMTP account would let one leaked credential reach both email and the backups. |
| **P2** | **Roles on the shared drive:** the nightly account as **Content manager**, and **not** Manager; at least two **Managers** who are people staying on the team. Sharing outside the organization turned off for this drive. | A Content manager can prune, which moves files to the trash, but can't delete permanently, so a compromised host can't erase history for good (§1). With only one Manager, the drive can end up with nobody able to change its members or restore from the trash. |
| **P3** | **Approval for the data.** The dump contains personal information (names, emails, password hashes, session data), encrypted on our side. If the drive is in UAlberta's Workspace, check the university's data classification for Google Workspace, and where the data is stored. | Encryption doesn't replace approval. Get it before the first upload. |
| **P4** | **Enough space.** Needed ≈ one night's backups × 120 (90 days kept + 30 days in the trash). One night's size: `du -ch $(ls -t ~/backups/db-*.dump \| head -1) $(ls -t ~/backups/media-*.tar.gz \| head -1) \| tail -1`. Ask whether the drive has its own limit. | A full drive makes every upload fail (§6). |
| **P5** | **Permission to create an OAuth client**, with an **Internal** consent screen, in a Google Cloud project in the same Workspace as the shared drive. Also ask whether the Admin console (Security → API controls) blocks unapproved apps. If it does, the admin has to allow the client ID from §3.2. | Tokens from an **External** app left in **Testing** expire after 7 days. Internal apps don't have that limit. |

### 2.2 Set-up record

Fill this in as you go, and commit it. Don't put secrets here: no
passwords, client secrets or tokens.

| Item | Value |
| --- | --- |
| Workspace that owns the shared drive | |
| Shared drive name and ID (§3.1) | |
| Nightly account (P1: a, b or c) | |
| Managers of the shared drive | |
| Data approval (P3): who, and when | |
| Space available / estimated need (P4) | |
| Google Cloud project, and who owns it (§3.2) | |
| OAuth client ID (not the secret) | |
| Where the secrets are stored (§3.7) | |
| Monitoring check URL owner (§3.11) | |
| Set up on `cossee-web-server` by, and date | |
| First restore from Drive tested (§3.10), date | |

---

## 3. Set-up, step by step

Run the server steps as `deploy` from `~/i-connects`, on `main` with this
change merged (`git pull` first). Each step says whether it happens on the
server, on your laptop, or in a browser.

### 3.1 Shared drive (browser)

1. The Manager creates the shared drive (e.g. `i-connects backups`), or
   uses the one already decided on.
2. **Manage members:** add the nightly account as **Content manager**
   (not Manager), and make sure there are two
   Managers (P2). For a service account (P1 b), add its
   `…@….iam.gserviceaccount.com` address.
3. **Drive settings** for the shared drive: turn off sharing with people
   outside the organization.
4. Open the shared drive in the browser. Its **ID** is the last part of
   the URL, `https://drive.google.com/drive/folders/<ID>`. Record it in
   §2.2.

Don't create the `i-connects-backups` folder by hand. rclone has to create
it itself (§3.5), because the job can only see files and folders it
created (§10).

### 3.2 OAuth client (browser)

Skip this step for a service account (P1 b). Instead, create a key for it
in the same project, as a JSON file, and use it in §3.5.

In the [Google Cloud console](https://console.cloud.google.com), signed in
as a team member of the Workspace that owns the drive:

1. Create a project, e.g. `i-connects-backups`, owned by the team rather
   than one person (add a second Owner). Record it in §2.2.
2. **APIs & Services → Library:** enable the **Google Drive API**.
3. **APIs & Services → OAuth consent screen:** user type **Internal**; app
   name `i-connects backups`; add the scope
   `https://www.googleapis.com/auth/drive.file`. If Internal isn't
   offered, the project isn't in the Workspace. Fix that rather than
   choosing External. If External really is the only option, press
   **Publish app** so the status is **In production**, not Testing.
4. **APIs & Services → Credentials → Create credentials → OAuth client
   ID**, type **Desktop app**, name `cossee-web-server offsite-sync`.
   Record the client ID in §2.2, and put the **client secret** in the
   secret store (§3.7).
5. If the Workspace blocks unapproved apps (P5), send the client ID to
   the admin to allow.

### 3.3 Install rclone (server)

```bash
sudo apt-get install -y rclone
rclone version | head -1        # 1.60 or later; the script refuses anything older
curl -sSI https://www.googleapis.com/drive/v3/about | head -1   # any HTTP status line (401 is fine) means the host can reach Google
```

Install rclone on your laptop too, the same version or newer
(`brew install rclone`, the distro package, or rclone.org).

### 3.4 Remote `gdrive`: the shared drive (server + laptop)

On the server:

```bash
rclone config
```

Answer the prompts like this:

| Prompt | Answer |
| --- | --- |
| `n/s/q>` | `n` (new remote) |
| `name>` | `gdrive` |
| `Storage>` | `drive` |
| `client_id>` / `client_secret>` | from §3.2 (leave both empty for a service account) |
| `scope>` | `drive.file` |
| `service_account_file>` | empty; for a service account, the JSON key's path, e.g. `/home/deploy/.config/i-connects/gdrive-sa.json` (`chmod 600` it) |
| `Edit advanced config?` | `n` |
| `Use web browser to automatically authenticate?` | `n` (the server has no browser) |

rclone then prints a line like
`rclone authorize "drive" "eyJjbGllbnRfaWQiOi…"`. **Run exactly that
line on your laptop.** The long string carries the client ID and the
`drive.file` scope, so the token gets exactly that scope. A bare
`rclone authorize "drive"` would use rclone's own client instead. A browser opens: sign in as the **nightly account**
(P1) and allow access. The laptop prints a token (`{"access_token":…}`).
Paste it at the server's `config_token>` prompt.

| Prompt | Answer |
| --- | --- |
| `Configure this as a Shared Drive (Team Drive)?` | `n`: with the `drive.file` scope rclone can't list shared drives, so set the ID in the next command |
| `Keep this "gdrive" remote?` | `y` |

Then point the remote at the shared drive, and let rclone create the
folder:

```bash
rclone config update gdrive team_drive <shared drive ID from §3.1> config_refresh_token=false --non-interactive > /dev/null
rclone config show gdrive       # must include team_drive = <that ID>
rclone mkdir gdrive:i-connects-backups
rclone lsd gdrive:              # must list i-connects-backups
```

If `mkdir` fails with `notFound` / `File not found` for the drive ID,
first check that the ID is right and the account is a member. If both are
right, `drive.file` isn't enough on this Workspace. Switch to the full
scope with `rclone config update gdrive scope drive config_refresh_token=false --non-interactive`, then
`rclone config reconnect gdrive:` (another `rclone authorize` on the
laptop). The full scope is acceptable only if the nightly account can open
nothing but this shared drive (P1). Note the change in §2.2.

### 3.5 Remote `gdrive-crypt`: the encryption layer (server)

Generate two long random passwords first. You'll type them into rclone
now, and put them in the secret store in §3.7:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # password
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # password2 (salt)
```

```bash
rclone config
```

| Prompt | Answer |
| --- | --- |
| `n/s/q>` | `n` |
| `name>` | `gdrive-crypt` |
| `Storage>` | `crypt` |
| `remote>` | `gdrive:i-connects-backups` |
| `filename_encryption>` | `off` |
| `directory_name_encryption>` | `false` |
| `Password or pass phrase for encryption` | `y` (type my own), then the first password |
| `Password or pass phrase for salt` | `y`, then the second password |
| `Edit advanced config?` | `n` |
| `Keep this remote?` | `y` |

```bash
chmod 600 ~/.config/rclone/rclone.conf
```

The finished `~/.config/rclone/rclone.conf` looks like this (the values
are rclone's obscured form, not the plaintext):

```ini
[gdrive]
type = drive
client_id = <…>.apps.googleusercontent.com
client_secret = <…>
scope = drive.file
token = {"access_token":"…","refresh_token":"…",…}
team_drive = <shared drive ID>

[gdrive-crypt]
type = crypt
remote = gdrive:i-connects-backups
filename_encryption = off
directory_name_encryption = false
password = <obscured>
password2 = <obscured>
```

With `filename_encryption = off`, names stay readable, and rclone adds
`.bin` to each name on Drive: the web page shows
`db-2026-09-28T030001.dump.bin`. The contents are ciphertext.

### 3.6 Settings file (server)

```bash
mkdir -p ~/.config/i-connects && chmod 700 ~/.config/i-connects
cat > ~/.config/i-connects/offsite-gdrive.env <<'EOF'
OFFSITE_REMOTE=gdrive-crypt:cossee-web-server
OFFSITE_NAME=gdrive
OFFSITE_RETENTION_DAYS=90
OFFSITE_KEEP_MIN=14
# OFFSITE_PING_URL=https://hc-ping.com/<uuid>   # §3.11
# OFFSITE_BWLIMIT=4M                            # optional: cap upload speed on the shared host
EOF
chmod 600 ~/.config/i-connects/offsite-gdrive.env
```

Each nightly run prunes after its uploads verify. Pruned copies go to the
shared drive's trash, never straight to permanent deletion.

§4 lists every setting.

### 3.7 Store the secrets off the host (secret store)

This backup exists for the case where this VM is gone, so everything
needed to read it must also exist somewhere else. Put these in the team
secret store, next to `.env`:

- the two **crypt passwords**, in plaintext, as typed in §3.5. **If these
  are lost, every off-site backup is unreadable.**
- the **OAuth client ID and secret** (§3.2), or the **service account's
  JSON key**;
- the **shared drive ID**;
- a copy of `~/.config/i-connects/offsite-gdrive.env`;
- for a functional account (P1 a): its password and 2-Step Verification
  recovery details, held by the team rather than one person.

The OAuth token itself doesn't need storing. A new one can be made at any
time (§5).

Record where the secrets are in §2.2.

### 3.8 First run (server)

```bash
E=~/.config/i-connects/offsite-gdrive.env
scripts/offsite-sync.sh --env $E --dry-run   # "would upload: …" for every backup; nothing is written
scripts/offsite-sync.sh --env $E             # uploads the whole ~/backups history
scripts/offsite-sync.sh --env $E             # must say "nothing new to upload"
scripts/offsite-sync.sh --env $E --list      # what the shared drive holds
```

The first real run ends with `done: uploaded <n> files`, after a
`verifying <n> uploaded files (cryptcheck)` line. In the browser, the
shared drive should now have
`i-connects-backups/cossee-web-server/db-*.dump.bin` and
`media-*.tar.gz.bin`.

### 3.9 Cron (server)

`crontab -e` as `deploy`, and **replace** the Part K backup line with:

```cron
0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1 && scripts/offsite-sync.sh --env ~/.config/i-connects/offsite-gdrive.env >> ~/backups/offsite-gdrive.log 2>&1
```

- `&&` means a failed backup doesn't trigger an upload.
- Missed nights catch up on their own: each run uploads **every** local
  backup Drive lacks, not just tonight's. The local 30-day retention is
  the window for fixing a broken off-site job before backups start leaving
  the host without an off-site copy.

The next morning, `tail ~/backups/offsite-gdrive.log` should end with
`done: uploaded 2 files`.

Rotate the new log along with `backup.log`, which also grows without
limit:

```bash
sudo tee /etc/logrotate.d/i-connects-backups > /dev/null <<'EOF'
/home/deploy/backups/*.log {
    monthly
    rotate 12
    compress
    missingok
    notifempty
    copytruncate
    su deploy deploy
}
EOF
sudo logrotate --debug /etc/logrotate.d/i-connects-backups   # dry run: no errors
```

### 3.10 Prove a restore from Drive (server)

Until a file downloaded from Drive has been restored, don't count the
off-site backup as working:

```bash
E=~/.config/i-connects/offsite-gdrive.env
mkdir -p ~/restore-test
scripts/offsite-sync.sh --env $E --fetch db-<newest stamp>.dump --to ~/restore-test
scripts/restore.sh --db ~/restore-test/db-<newest stamp>.dump --target drill_$(date +%Y%m) --yes
docker compose exec -T db psql -U iconnects -d drill_$(date +%Y%m) -c 'select count(*) from accounts_user;'
docker compose exec -T db dropdb -U iconnects drill_$(date +%Y%m)
rm -r ~/restore-test
```

Record the date in §2.2.

### 3.11 Alert when it stops (browser + server)

Create a check at a dead-man's-switch service
([Healthchecks.io](https://healthchecks.io) works on its free plan): period
1 day, grace 2 hours, alerts to the team's email. Put its ping URL in
`OFFSITE_PING_URL` in the settings file. The job pings only after a fully
successful run, and a failed `backup.sh` means the sync doesn't run at
all, so both kinds of failure raise the alert.

Test it once: comment the setting out for a night and confirm the alert
arrives, then put it back.

---

## 4. Reference: `scripts/offsite-sync.sh`

```
scripts/offsite-sync.sh [--env FILE] [--dry-run] [--no-prune]     nightly sync
scripts/offsite-sync.sh [--env FILE] --prune-only [--dry-run]     prune only, e.g. after lowering the retention
scripts/offsite-sync.sh [--env FILE] --list                       what the remote holds
scripts/offsite-sync.sh [--env FILE] --fetch NAME... [--to DIR]   download for a restore
```

| Option | Effect |
| --- | --- |
| `--env FILE` | settings file [`~/.config/i-connects/offsite.env`] |
| `--dry-run` | show what would be uploaded or pruned; write nothing, don't touch the marker, don't ping |
| `--no-prune` | sync without pruning, whatever `OFFSITE_PRUNE` says |
| `--prune-only` | only the prune step: no upload, no marker, no ping |
| `--list` | `rclone lsl` of the remote, plus counts; fails on duplicate names |
| `--fetch NAME...` | download these backups (bare names, as `--list` shows them), decrypted, into `BACKUP_DIR` or `--to DIR`. Refuses to overwrite a local file |

Settings (in the `--env` file):

| Variable | Default | Meaning |
| --- | --- | --- |
| `OFFSITE_REMOTE` | **required** | rclone destination, e.g. `gdrive-crypt:cossee-web-server` |
| `OFFSITE_NAME` | `offsite` | names the lock (`BACKUP_DIR/.<name>.lock`) and marker (`BACKUP_DIR/.<name>-last-ok`), and prefixes log lines |
| `BACKUP_DIR` | `~/backups` | local backups to copy (the same default as `backup.sh`) |
| `OFFSITE_RETENTION_DAYS` | `90` | prune copies whose name dates are older than this |
| `OFFSITE_KEEP_MIN` | `14` | skip the whole prune if fewer db dumps than this would remain |
| `OFFSITE_PRUNE` | `yes` | `no`: a sync never prunes (for an account without delete rights, e.g. a Contributor) |
| `OFFSITE_VERIFY` | `cryptcheck` | `cryptcheck` compares with the remote's stored hashes (Drive); `download` reads each new file back (remotes without hashes, e.g. SMB) |
| `OFFSITE_BWLIMIT` | unset | rclone `--bwlimit`, e.g. `4M` |
| `OFFSITE_PING_URL` | unset | fetched after a fully successful sync |

`RCLONE_CONFIG` selects the rclone config file, as it does for rclone
itself.

**Exit status:** 0 on success. Non-zero, with a `FAILED:` line, for:
- missing settings or an old rclone;
- another run holding the lock;
- no backups to copy;
- a listing, upload, check, delete or download error;
- a remote file that differs from the local one;
- duplicate names on the remote.

A failed ping after a good sync is logged as a warning and doesn't fail
the run.

**Files it writes:**
- `BACKUP_DIR/.<name>.lock`;
- `BACKUP_DIR/.<name>-last-ok`;
- `BACKUP_DIR/.<name>-unverified`, only while an upload has failed
  verification. Those files are checked again on each run until they pass;
- a temporary directory, removed on exit. It never changes or deletes
anything in `BACKUP_DIR` itself.

**Tests:** `scripts/tests/test-offsite-sync.sh` runs every behaviour above
against a local rclone remote wrapped in the same crypt layer. No Google
account is needed. CI runs it in the **Off-site backup script** job,
together with `shellcheck`.

---

## 5. Restore from Google Drive

For when the host is gone or `~/backups` is unusable. On a rebuilt host,
first follow `DEPLOYMENT.md` Parts B–H. The host only needs outgoing
HTTPS. Then:

1. Install rclone (§3.3).
2. Recreate the two remotes from the secret store: §3.4 with the **same
   OAuth client** and a fresh `rclone authorize` (any account that is a
   member of the drive can read), then §3.5 with the **same two crypt
   passwords**. Restore `offsite-gdrive.env` from the secret store.
3. Find and fetch the backup:
   ```bash
   E=~/.config/i-connects/offsite-gdrive.env
   scripts/offsite-sync.sh --env $E --list
   scripts/offsite-sync.sh --env $E --fetch db-<stamp>.dump media-<stamp>.tar.gz   # → ~/backups/
   ```
4. Restore with the existing tool:
   ```bash
   scripts/restore.sh --db ~/backups/db-<stamp>.dump --media ~/backups/media-<stamp>.tar.gz --yes
   ```
5. Continue with Part I (post-launch verification), then set the
   off-site job up again (§3.6, §3.9).

**If the OAuth client or its Cloud project is gone,** a new client with
`drive.file` can't see the old files. Authorize a new client with
`scope = drive` instead (`rclone config update gdrive scope drive config_refresh_token=false --non-interactive`, then
`rclone config reconnect gdrive:`). The full scope sees everything the
account can open, including the backups.

**Files downloaded by hand** from the Drive web page are still encrypted
(`*.bin`). Put them in a folder, and decrypt them with the two plaintext
passwords from the secret store:

```bash
rclone copy ":crypt,remote=$HOME/Downloads/enc,filename_encryption=off,password=$(rclone obscure '<password>'),password2=$(rclone obscure '<password2>'):" ./decrypted
```

**A copy pruned by mistake** stays in the shared drive's trash for 30
days. A Manager, or a Content manager such as the nightly account, can
restore it from the trash in the web page, or with
`rclone backend untrash gdrive:i-connects-backups` on the server.

---

## 6. Monitoring and troubleshooting

- **Marker:** `stat -c %y ~/backups/.gdrive-last-ok`. Older than about 26
  hours means the job is failing.
- **Alert:** the dead-man's switch from §3.11.
- **Log:** `~/backups/offsite-gdrive.log`. Every line starts with a
  timestamp and `offsite-sync[gdrive]:`. Failures are `FAILED: …` lines,
  with rclone's own message above them.
- **Space:** monthly, run `rclone size gdrive-crypt:cossee-web-server`,
  and compare it with the drive's limit (P4). The trash counts too, and
  `rclone size` doesn't include it.

| Symptom in `offsite-gdrive.log` | Likely cause | Action |
| --- | --- | --- |
| `invalid_grant` / `Token has been expired or revoked` | an External app left in Testing (7-day expiry), a revoked token, or a suspended account | Make the app Internal or In production (§3.2), then `rclone config reconnect gdrive:` with a new `rclone authorize` on a laptop |
| `access_denied` / `admin_policy_enforced` | the Workspace blocks the app or the scope | The admin allows the client ID (P5) |
| `403 rateLimitExceeded` / `userRateLimitExceeded` | too many API calls, usually because `client_id` is empty and rclone's shared client is in use | rclone retries by itself; if it keeps happening, use our own client (§3.2) |
| `403 storageQuotaExceeded` | the shared drive or the Workspace's storage is full, trash included | Empty old trash (a Manager), lower `OFFSITE_RETENTION_DAYS`, or ask the admin (P4) |
| `insufficientFilePermissions` or `could not delete` during a prune | the nightly account's role was lowered below Content manager | A Manager restores the role (P2); until then, `OFFSITE_PRUNE=no` in the settings file keeps the uploads running |
| `notFound` / `directory not found` | wrong `team_drive` ID, the account was removed from the drive, or the folder was made by hand or by another OAuth client (§10) | Check the ID and membership; check that `client_id` matches the one in the secret store |
| `FAILED: upload failed` with `immutable` above it | a file on Drive has a different size from the local file with the same name | **Investigate before doing anything else**: possible corruption or tampering. Don't delete either copy |
| `FAILED: verification failed` | an uploaded file doesn't match its local copy | Same as above. The run stops before pruning and doesn't touch the marker. The file names go in `~/backups/.gdrive-unverified`, and every later run checks them again until they pass. To check everything by hand: `rclone cryptcheck --one-way ~/backups gdrive-crypt:cossee-web-server` |
| `FAILED: unexpected error on line <n>` | a command failed where the script has no specific message | Read the lines above it; run the script by hand with the same `--env` to reproduce |
| `FAILED: duplicate names` | an interrupted upload, or someone uploading by hand | `rclone lsl gdrive-crypt:cossee-web-server`, keep the copy that passes `cryptcheck`, and delete the other in the web page (Content manager or Manager) |
| `FAILED: another gdrive run holds …lock` | the previous night's run is still going, or hung | `ps aux \| grep offsite-sync`; if it's hung, kill it and run once by hand |
| `prune skipped (keep-min guard)` | fewer than `OFFSITE_KEEP_MIN` recent dumps on Drive | Expected in the first two weeks; after that, backups have been failing, so check `backup.log` |

---

## 7. Routine tasks

### 7.1 Monthly: check the drive (browser + server)

Drive empties the trash 30 days after a file goes in, so don't let this
slip past a month. Recent backups trashed by mistake come back by
themselves, because the next nightly run re-uploads anything still in
`~/backups` (the last 30 days). Older copies exist only in the trash.

- **Trash:** a Manager opens the shared drive's **Trash**. Everything in
  it from `i-connects-backups` should match a `prune: deleted …` line in
  `~/backups/offsite-gdrive.log` (`grep 'prune: deleted' ~/backups/offsite-gdrive.log | tail -30`).
  Anything else, above all recent backups, means someone or something
  with the nightly credential trashed them: restore them, and treat it as
  a host compromise (Part M).
- **Space:** `rclone size gdrive-crypt:cossee-web-server` against the
  drive's limit (P4).
- **Members:** the nightly account is still a Content manager and not a
  Manager, and there are still two Managers (P2).

### 7.2 Quarterly: restore drill from Drive (server)

Do §3.10 again with the newest dump **from Drive**, not from `~/backups`.
Also compare that one file with the local copy by reading it back:

```bash
echo db-<stamp>.dump > /tmp/one && rclone check --download --one-way --files-from /tmp/one ~/backups gdrive-crypt:cossee-web-server
```

This proves the token, the OAuth client and the encryption key all still
work. At the same time, check the shared drive's members (P2): the roles
are still right, and two Managers are still here.

### 7.3 People leaving

Add these to the team's offboarding list:

- **The Google Cloud project owner:** add another Owner before they
  leave.
- **A Manager of the shared drive:** add another, so there are always
  two.
- **The nightly account, if it's a person's (P1 c):** switch to another
  account before theirs is closed. Add the new account as Content manager, run
  `rclone config reconnect gdrive:` on the server with a new
  `rclone authorize` on a laptop, then remove the old account from the
  drive.
- **Whoever holds the functional account's 2-Step Verification recovery:**
  update it.

### 7.4 Rotating credentials

| What | When | How |
| --- | --- | --- |
| OAuth token (nightly) | suspected leak | Revoke it: myaccount.google.com → Security → Third-party connections, as the nightly account, or the Workspace admin console. Then `rclone config reconnect gdrive:` on the server. Files don't change |
| OAuth client secret | suspected leak | Cloud console → Credentials → reset the secret. `rclone config update gdrive client_secret <new> config_refresh_token=false --non-interactive`, then `rclone config reconnect gdrive:`. Update the secret store |
| Service account key | yearly, or suspected leak | Create a new key, replace the JSON file, delete the old key in the console |
| Crypt passwords | suspected leak (treat like a `.env` leak, Part M) | Create a new `gdrive-crypt` remote with new passwords, pointed at a **new folder**, e.g. `gdrive:i-connects-backups-2`. Change `OFFSITE_REMOTE`, run a sync (it uploads everything again), and prove a restore (§3.10). Then delete the old folder (the nightly account can, as a Content manager). Update the secret store |

---

## 8. Running alongside RDS

The same script serves the UAlberta Research Data Storage plan
(`RDS_BACKUP.md`, on branch `claude/ecstatic-hawking-sr34ja`). Give that
destination its own settings file with `OFFSITE_NAME=rds` and
`OFFSITE_VERIFY=download`, because SMB stores no checksums. Chain both
after the backup:

```cron
0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1 && { scripts/offsite-sync.sh --env ~/.config/i-connects/offsite-rds.env >> ~/backups/offsite-rds.log 2>&1; scripts/offsite-sync.sh --env ~/.config/i-connects/offsite-gdrive.env >> ~/backups/offsite-gdrive.log 2>&1; }
```

Each destination has its own lock, marker and log, and the `;` means Drive
still runs when RDS fails. Use different crypt passwords for the two, so
one leaked key doesn't expose both copies.

---

## 9. Acceptance checklist

Off-site backups are "done" (per Part K) only when every box is ticked:

- [ ] §2.2 set-up record filled in and committed, including the data approval (P3)
- [ ] Nightly account is a functional or service account, **not** a person's, or the risk is accepted in writing (P1)
- [ ] Nightly account is a Content manager, **not** a Manager, and the drive has two Managers who are staying (P2)
- [ ] Confirmed on the real drive that the nightly account can't delete for good: `echo x > /tmp/probe && rclone copy /tmp/probe gdrive:i-connects-backups && rclone deletefile --drive-use-trash=false gdrive:i-connects-backups/probe` must **fail**. Then `rclone deletefile gdrive:i-connects-backups/probe` (to the trash) must succeed
- [ ] Space covers 90 days of copies plus 30 days of trash (P4)
- [ ] OAuth consent screen is **Internal** (or External and In production)
- [ ] Crypt passwords, OAuth client and settings file stored in the team secret store (§3.7)
- [ ] Second run says "nothing new to upload"; `--list` shows the full `~/backups` history (§3.8)
- [ ] Cron line from §3.9 installed, log rotation in place, and the next morning's log shows a clean run
- [ ] A run more than 7 days after set-up still succeeds (proves the token doesn't expire)
- [ ] Dead-man's switch set and tested once (§3.11)
- [ ] One file restored from Drive with `restore.sh --target` and spot-checked (§3.10)
- [ ] `scripts/offsite-sync.sh --env … --prune-only --dry-run` runs cleanly on the server. The first real prune comes about 90 days in; then check the trash against the log (§7.1)
- [ ] The quarterly drill (§7.2) and offboarding items (§7.3) added to the team's lists

---

## 10. Design notes

Why the script works the way it does. Change these only on purpose.

- **rclone, no mount.** The Drive API is called directly as `deploy`.
  There's no FUSE mount, no root, and nothing stale to recover on a shared
  host. The same script works on any rclone remote, which is how it also
  serves RDS.
- **`drive.file` scope.** The token reaches only files and folders this
  OAuth client created, so a leaked token can't read anything else the
  account can open. The costs: rclone must create the folder itself; the
  same client ID is needed to see the files later; and rclone can't list
  shared drives, so `team_drive` is set by ID.
- **Encryption on the host.** Drive only ever holds ciphertext.
  `filename_encryption = off` keeps names readable (they contain only
  timestamps), so pruning by date and picking a restore stay simple.
- **`copy`, never `sync`.** With `sync`, an emptied `~/backups` would empty
  Drive the next night.
- **`--immutable --size-only`.** Every local backup is compared with its
  remote copy on every run, so a changed copy fails loudly instead of
  being silently skipped or replaced. It compares sizes, not modification
  times, because backends keep times differently. Content is checked by
  hash on upload, and by download in the quarterly drill.
- **Verify this run's uploads, plus any that failed before.** `cryptcheck`
  against Drive's MD5s is cheap, but it still reads every local file, so
  files already verified are left to the drill. A file that failed
  verification is listed in `.<name>-unverified` until it passes, so it
  can't be taken for good just because it is already there.
- **Prune by the name's date, behind a keep-minimum.** This doesn't depend
  on remote modification times, and a run of failed backups can't cause
  the last good copies to be pruned. Files whose names aren't backup names
  are never touched.
- **Content manager, not Manager, nightly.** The server can prune on its
  own, but Drive only lets it move files to the trash. Deleting for good
  and emptying the trash need a Manager, a credential that never sits on
  the server. The worst a compromised host can do is trash the history,
  which a Manager can undo for 30 days. The monthly trash check (§7.1) is
  what makes that window count.
