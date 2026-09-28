# Off-site Backups to Google Drive — plan

**Status: plan only, nothing here is implemented yet.** This closes the gap
named in `DEPLOYMENT.md` Part K ("Off-host copy"): the nightly
`scripts/backup.sh` writes to `~/backups` on `cossee-web-server`, and if that
host is lost, the backups go with it. This plan copies every new backup to
**Google Drive** right after the nightly backup finishes.

It follows the same design as `RDS_BACKUP.md` (the UAlberta Research Data
Storage plan, on branch `claude/ecstatic-hawking-sr34ja`): the same
`rclone`-based, encrypted, copy-only script chained after `backup.sh`, the
same safety rules, and the same restore path. Google Drive can be used
**instead of** RDS (for example if the RDSS login question, RDS Q1, has no
good answer), or **as well as** RDS. §3.7 covers running both.

Sources: the rclone [Google Drive backend](https://rclone.org/drive/) and
[crypt backend](https://rclone.org/crypt/) docs, Google's OAuth 2.0 docs
(refresh-token expiry), and the Google Drive Help pages on shared drive
roles and trash. §2 lists what these confirm and what still needs an answer.

Contents:

1. [What it does](#1-what-it-does)
2. [What's known, and what's still open](#2-whats-known-and-whats-still-open)
3. [Design](#3-design)
4. [Implementation steps](#4-implementation-steps)
5. [Operator instructions (after implementation)](#5-operator-instructions-after-implementation)
6. [Restore from Google Drive](#6-restore-from-google-drive)
7. [Monitoring and failure modes](#7-monitoring-and-failure-modes)
8. [Acceptance checklist](#8-acceptance-checklist)
9. [Changes to other docs](#9-changes-to-other-docs)

---

## 1. What it does

1. At 03:00 cron runs `scripts/backup.sh`, which writes
   `~/backups/db-<stamp>.dump` and `~/backups/media-<stamp>.tar.gz` and
   checks that both are readable. This is how it works today.
2. **New:** if the backup succeeded, cron runs `scripts/offsite-sync.sh`
   with the Google Drive settings. It talks to the Drive API over HTTPS
   using `rclone`, without mounting anything, and:
   - uploads every finished backup file in `~/backups` that Drive doesn't
     have yet, **encrypted on the host before it leaves**;
   - checks what it just uploaded against the local file, using the
     checksums Drive stores for every file;
   - removes Drive copies older than the off-site retention period
     (default 90 days). It never deletes the newest copies, and it only
     prunes after a successful upload. Deleted copies go to Drive's trash
     first, so a bad prune can be undone for 30 days;
   - on success, updates a marker file and (optionally) pings a monitoring
     URL, so that a job that stops working gets noticed.
3. The job is **copy-only**: deleting or corrupting files in `~/backups`
   does not change what is on Drive. Old Drive copies are removed only by
   the prune step, which works from the date in each file's name.

Two copies, two retention periods:

| Where | Written by | Retention | Protects against |
| --- | --- | --- | --- |
| `~/backups` on `cossee-web-server` | `scripts/backup.sh` | 30 days (`RETENTION_DAYS`) | bad deploy or migration, accidental deletes; the fastest restore |
| Google Drive folder | `scripts/offsite-sync.sh` | 90 days (`OFFSITE_RETENTION_DAYS`) | losing the VM or its disk, host compromise, mistakes noticed late |

**Google Drive is not a backup of itself.** Drive keeps deleted files in
the trash for 30 days, and keeps old versions of a file that is overwritten,
but anyone holding the job's credential can empty the trash or delete files
permanently. The rules in §3.4, and the shared-drive roles in Q5, are what
keep the off-site copies safe from a compromised host. VM disk snapshots
(Part K) are still worth having.

---

## 2. What's known, and what's still open

### 2.1 Confirmed by the documentation

| Topic | What the docs say | What it means for this plan |
| --- | --- | --- |
| Protocol | rclone's `drive` backend uses the Drive API over HTTPS (TCP 443). | No mount, no root, no VPN. The host's UFW already allows outgoing traffic (Part B4), and the rebuilt host in §6 can be anywhere. |
| Login for an unattended job | rclone stores an OAuth **refresh token** in `rclone.conf`. The token is created once, in a browser, with `rclone authorize "drive"` on a laptop, then pasted into the server's config. | No password is stored on the host. The token can be revoked on its own, without changing the account's password. |
| Token expiry | Google expires refresh tokens after **7 days** for OAuth apps whose consent screen is **External** with publishing status **Testing**. Tokens from **Internal** apps (Workspace only) or External apps set to **In production** don't have this limit. Tokens also expire after 6 months unused, or when revoked. | The OAuth client must be Internal or In production (§3.2), or the job stops working after a week. A nightly job never hits the 6-month limit. Changing the account's password doesn't revoke a Drive-only token (only tokens that include Gmail scopes). |
| OAuth client | rclone ships a shared client ID, but its quota is shared by every rclone user and is often rate-limited. The rclone docs recommend creating your own. | Create a Google Cloud project and OAuth client for this job (§4 step 3). Whoever owns that project matters for offboarding (§7). |
| Scopes | `drive` (everything in the account) or `drive.file` (only files and folders this OAuth client created). | Use `drive.file`: a leaked token can reach the backups but nothing else in the account. See the note in §3.2 about restores. |
| Service accounts | Service accounts have no Drive storage of their own. They can only write to a **shared drive** they're a member of. | Service accounts only fit option (a) of Q1, and only if the org allows service-account keys and sharing with them. OAuth on a real account works everywhere, so it's the default. |
| Checksums | Drive stores an MD5 checksum for every uploaded file. With a `crypt` remote on top, `rclone cryptcheck` can compare local files with the encrypted copies without downloading them. | Verification is cheaper than on RDS, where SMB has no checksums (§3.4 rule 4). |
| Duplicate names | Drive allows two files with the same name in one folder. | `--list` reports duplicates, and the job treats one as an error to investigate (§3.4, §7). |
| Trash | By default rclone moves deleted files to the trash (`--drive-use-trash`, on by default). Drive empties the trash after 30 days. Trash counts against the storage quota. | Pruning gets a 30-day undo window. Plan quota for about 30 extra days of copies (Q3). |
| Shared drive roles | In a shared drive, a **Contributor** can add and edit files but can't move them to the trash or delete them. A **Content manager** can. Files in a shared drive belong to the organization, not to a person. | This is how the nightly job gets write-but-not-delete (§3.4 rule 8), the same idea as RDS Q5. It's only possible with option (a) of Q1. |
| Upload limit | 750 GB of uploads per user per day. | Far above what this job needs. `~/backups` today is a few GB at most (`du -sh ~/backups`). |

### 2.2 Still open — decide, or ask the account's admin

| # | Question | Why it matters |
| --- | --- | --- |
| **Q1** | **Which Google account and which drive?** In this order of preference: (a) a **shared drive** in a Google Workspace the team belongs to (UAlberta's, or a `cossee.org` one if it exists), with the nightly job logged in as a functional account (not a person's), or as a service account, added as **Contributor**; (b) a **dedicated** Google account, used for nothing but these backups, writing to a folder in its own My Drive; (c) a named person's UAlberta or personal account, with the risks written down (below). | With (c), the backups sit in a person's My Drive. When that person leaves and their account is closed, **the files are deleted with it**. A shared drive (a) survives people leaving. Don't reuse the Gmail account from Part F1 either: one leaked credential would then reach both email and the backups, and backups would eat its 15 GB. |
| **Q2** | **Is Google Drive approved for this data?** The dump contains personal information (names, emails, password hashes, session data), **encrypted on our side before upload**. If the account is UAlberta's, check the university's data classification for Google Workspace. Where is the data stored (country)? | Encryption is planned either way (§3.3), but it doesn't replace approval. A personal `gmail.com` account (option b) may not be acceptable for UAlberta users' data at all. Find out before uploading anything. |
| **Q3** | **How much space is there?** A free consumer account has 15 GB shared across Gmail, Drive and Photos. Workspace accounts and shared drives have whatever quota the admin sets. Size needed ≈ (size of one night's db dump + media tarball) × (90 days + 30 days of trash). | If it doesn't fit: lower `OFFSITE_RETENTION_DAYS`, prune with `--drive-use-trash=false` (loses the undo window), buy more storage, or ask the admin. |
| **Q4** | **Can we create an OAuth client for this?** A Workspace admin may block unapproved third-party apps, or restricted Drive scopes, under Admin console → Security → API controls. Can the app be **Internal** (Workspace), or must it be External and set **In production**? | If apps are blocked, the admin has to allow this client ID. If the app stays in Testing, the token dies after 7 days (§2.1). |
| **Q5** | With a shared drive (Q1 a): can the nightly account be a **Contributor**, with pruning done by a second account that is a **Content manager**? | Without it, a compromised host can delete every off-site copy and empty the trash. It's the same protection as RDS Q5 (§3.4 rule 8). |

**Record the answers in this file** (a short "Decided on <date>" table)
before starting §4 step 2.

---

## 3. Design

### 3.1 Tool: `rclone` with the `drive` backend

Use [`rclone`](https://rclone.org) with its built-in **`drive` backend**,
the same tool as the RDS plan. Ubuntu 24.04's `apt install rclone` ships
1.60.x, which has everything used here. Newer upstream releases carry
Drive API fixes, so prefer the upstream `.deb` if the apt version gives
trouble. Reasons:

- **No mount.** rclone calls the Drive API directly as the `deploy` user.
  There's no FUSE mount, no root and no stale mount to recover. This
  matters on a shared host.
- **Built-in client-side encryption** (a `crypt` remote wrapped around the
  Drive remote). See §3.3.
- **`copy --immutable` and `cryptcheck`** cover upload and verification,
  and `rclone lsf` / `deletefile` cover retention.
- **Same script as RDS.** Only the rclone remote and a few settings
  differ, so one `scripts/offsite-sync.sh` serves both (§3.7).

### 3.2 Configuration

Everything off-site is configured under `deploy`'s home directory, mode
`0600`. **Nothing goes in the app's `.env`.** The app never needs these
values, and `backup.sh` already reads nothing from `.env`.

```
~/.config/rclone/rclone.conf                 # remotes: "gdrive" (Drive) and "gdrive-crypt" (encryption wrapper)
~/.config/i-connects/offsite-gdrive.env      # settings for offsite-sync.sh (below)
```

`rclone.conf` (placeholders in `<>`; create it with `rclone config`, which
stores the passwords obscured):

```ini
[gdrive]
type = drive
client_id = <OAuth client ID from §4 step 3>
client_secret = <OAuth client secret>
scope = drive.file
token = <JSON from `rclone authorize "drive"` on a laptop>
# Option (a) of Q1, a shared drive: add its ID. Leave unset for My Drive.
# team_drive = <shared drive ID>

[gdrive-crypt]
type = crypt
remote = gdrive:i-connects-backups
filename_encryption = off          # keep db-<stamp>.dump names readable, so restores and pruning are simple
directory_name_encryption = false
password = <rclone obscure output>
password2 = <rclone obscure output>
```

**About `drive.file`:** rclone can only see files and folders that *this
OAuth client* created. So:

- Let rclone create the `i-connects-backups` folder (`rclone mkdir`, §5.1).
  A folder made by hand in the Drive web page is invisible to it.
- A restore on a rebuilt host must use the **same client ID** (it's in
  the secret store, §3.3). If that Google Cloud project is ever lost,
  authorize a new client with `scope = drive` instead; the full scope can
  see every file in the account, including the old backups.
- If `drive.file` turns out not to work with the shared drive in Q1 (a),
  use `scope = drive`. That's acceptable only because the account holds
  nothing but the backups (Q1).

`offsite-gdrive.env` variables (script defaults in brackets; the same
variables as the RDS plan, plus `OFFSITE_NAME`):

| Variable | Meaning |
| --- | --- |
| `OFFSITE_REMOTE` | rclone destination, e.g. `gdrive-crypt:cossee-web-server` **(required, no default)** |
| `OFFSITE_NAME` | short name used for the lock and marker files, e.g. `gdrive` [`offsite`] |
| `BACKUP_DIR` | local source [`~/backups`, same as `backup.sh`] |
| `OFFSITE_RETENTION_DAYS` | delete Drive copies older than this [`90`] |
| `OFFSITE_KEEP_MIN` | never prune below this many db dumps [`14`] |
| `OFFSITE_BWLIMIT` | optional rclone `--bwlimit`, so the job doesn't saturate the shared host's link [unset] |
| `OFFSITE_PING_URL` | optional URL to `curl` on success (Healthchecks.io-style dead-man's switch) [unset] |

### 3.3 Encryption and key custody

- Files are encrypted by rclone `crypt` **on the host, before upload**.
  Drive only ever holds ciphertext, so neither Google nor anyone else the
  folder is shared with can read user data.
- File names stay readable (`filename_encryption = off`). They contain
  only timestamps, and readable names keep the pruning and restore steps
  simple.
- **Store these outside the host**, in the same secret store as `.env`
  (Part K already says `.env` isn't in any backup on purpose):
  - the two crypt passwords (the plaintext typed into `rclone config`);
  - the OAuth client ID and secret, and which Google Cloud project they
    belong to;
  - the Google account's login and 2-Step Verification recovery details.

  If the crypt passwords are lost, **every off-site backup is
  unreadable**. Keeping them only on the VM defeats the point, because
  this backup exists for the case where the VM is gone. The refresh token
  itself doesn't need storing: a new one can be made with
  `rclone authorize` (§6).
- **Protect the Google account:** 2-Step Verification on, recovery email
  and phone that belong to the team (not one person), and the account used
  for nothing else (Q1).
- Treat a leak of the crypt passwords the same as a `.env` leak (Part M):
  rotate them, then upload everything again under the new key into a new
  folder. Delete the old folder once the new copies pass verification.
- A leaked **token** is cheaper to fix: revoke it (the account's
  myaccount.google.com → Security → Third-party connections, or the
  Workspace admin console), then run `rclone config reconnect gdrive:`.
  The files don't change.

### 3.4 Safety rules the script must follow

These are the same rules as the RDS plan. Only rules 4 and 8 differ.

1. **Never propagate deletions.** Use `rclone copy`, never `rclone sync`.
   With `sync`, an emptied or ransomwared `~/backups` would empty Drive on
   the next run.
2. **Don't overwrite on Drive.** Pass `--immutable`, so a file that
   already exists on Drive but differs is reported as an error, not
   replaced. Backup files never change after `backup.sh` renames them into
   place, so any mismatch means something is wrong.
3. **Upload only finished files.** Filter to `db-*.dump`,
   `media-*.tar.gz` and the legacy `db-*.sql.gz`. Exclude `*.tmp`, because
   `backup.sh` writes to `.tmp` and renames when done. Also exclude
   `backup.log`, the `offsite*.log` files and the dotfiles. Logs can
   contain paths and hostnames and aren't needed for a restore.
4. **Verify with Drive's checksums.** Run
   `rclone cryptcheck --one-way --files-from <this run's uploads>
   <BACKUP_DIR> <OFFSITE_REMOTE>`. It encrypts each local file the same
   way and compares the result with the MD5 Drive stored, without
   downloading. If it fails, exit non-zero and don't prune. The quarterly
   drill (§6) adds a full download check.
5. **Prune by the date in the file name, not the file's modified time.**
   List the remote with `rclone lsf`, parse the timestamp in
   `db-YYYY-MM-DDTHHMMSS.dump` and `media-…tar.gz`, and delete with
   `rclone deletefile` anything older than `OFFSITE_RETENTION_DAYS`.
   Pruning runs only after steps 1–4 succeed, and only if at least
   `OFFSITE_KEEP_MIN` db dumps would remain. Deleted files go to the trash
   (rclone's default), which gives 30 days to undo a mistake. If the
   listing shows two files with the same name, stop and don't prune (§7).
6. **Allow one run at a time.** Wrap the script in `flock` on
   `~/backups/.<OFFSITE_NAME>.lock`, so a slow upload can't overlap the
   next night's run.
7. **Keep the same shape as `backup.sh`:** `set -euo pipefail`, the same
   `log()`/`fail()` format with timestamped lines, and exit non-zero on any
   failure so cron and monitoring can see it.
8. **If Q5 is possible, use separate accounts for writing and deleting.**
   The nightly account is a **Contributor** on the shared drive, so it can
   add files but can't trash or delete them. Pruning (step 5) then runs
   under a second credential, a **Content manager**, that isn't stored in
   the nightly job's config, for example monthly and by hand with
   `--prune-only` (§4 step 3). A compromised host could then add junk but
   couldn't delete history. If Q5 isn't possible (options b and c of Q1),
   one account does both, and the 30-day trash is the only protection.

### 3.5 Fallback: the rclone shared client ID

Use this only if Q4 rules out creating an OAuth client. Leave `client_id`
and `client_secret` empty and rclone uses its own shared client. It works,
but:

- it shares an API quota with every other rclone user, so uploads can hit
  `403 rateLimitExceeded` / `userRateLimitExceeded` and have to retry
  (rclone does this by itself; a nightly job of a few files usually gets
  through);
- the team doesn't control the client. If rclone's client is ever
  blocked, or blocked by the Workspace admin, the job stops until someone
  re-authorizes with a new client;
- with `drive.file`, files created by the shared client are only visible
  to the shared client. Switching to our own client later means using
  `scope = drive` for the old files, or uploading everything again.

### 3.6 Scheduling

Chain the new script after the existing backup in the same cron entry, so
the off-site copy happens right after each successful backup:

```cron
0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1 && scripts/offsite-sync.sh --env ~/.config/i-connects/offsite-gdrive.env >> ~/backups/offsite-gdrive.log 2>&1
```

- `&&` means a failed backup doesn't trigger an upload. That run's
  failure shows up in `backup.log` and in monitoring (§7).
- Missed runs catch up: the script uploads **every** local file Drive
  lacks, not just tonight's. If one night's upload fails, for example
  because of a Google outage or an expired token, the next successful run
  uploads both nights' files. The local 30-day retention is the window for
  noticing and fixing a broken off-site job before backups start
  disappearing from the host without an off-site copy.

### 3.7 Running alongside RDS

If both plans are implemented, there is still **one** script. Each
destination has its own env file, log, lock and marker (`OFFSITE_NAME`), so
one failing doesn't block or hide the other:

```cron
0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1 && { scripts/offsite-sync.sh --env ~/.config/i-connects/offsite-rds.env >> ~/backups/offsite-rds.log 2>&1; scripts/offsite-sync.sh --env ~/.config/i-connects/offsite-gdrive.env >> ~/backups/offsite-gdrive.log 2>&1; }
```

The `;` inside the braces means Drive still runs when RDS fails. Use a
different crypt password for each destination, so one leaked key doesn't
expose both copies. If the RDS plan lands first with a fixed
`offsite.env` path, add `--env` and `OFFSITE_NAME` to it in the same PR
that adds Google Drive.

---

## 4. Implementation steps

Do these in order. Each code step can be its own PR.

1. **Choose the account and drive (Q1), and get approval (Q2).**
   - For option (a), ask the Workspace admin for a shared drive (e.g.
     `i-connects backups`) and the Contributor / Content manager split
     (Q5).
   - For option (b), create the dedicated Google account, turn on 2-Step
     Verification, and put the login in the secret store.
   - Check the quota against the size estimate in Q3.
2. **Record the answers** in §2.2 of this file.
3. **Create the OAuth client** in a Google Cloud project owned by the
   team (or by the Workspace, for option a):
   - enable the **Google Drive API**;
   - OAuth consent screen: **Internal** if the account is in a Workspace,
     otherwise **External** and then **Publish app** so its status is
     **In production** (not Testing, §2.1). An unverified External app
     shows a warning on the consent page; that's fine for our own account;
   - add the `https://www.googleapis.com/auth/drive.file` scope;
   - create credentials of type **Desktop app**, and store the client ID
     and secret in the secret store;
   - for option (a) with a Workspace that blocks third-party apps, have
     the admin allow this client ID (Q4).
4. **Add `scripts/offsite-sync.sh`** following §3.2–3.4, or extend the
   RDS plan's script if it has landed:
   - `--env FILE` (default `~/.config/i-connects/offsite.env`); fail
     clearly if `OFFSITE_REMOTE` is unset, `rclone` is missing, or its
     version is below 1.60;
   - `flock` → `rclone copy --immutable` with the include/exclude filters
     → `rclone cryptcheck --one-way` on this run's uploads → duplicate
     check → date-based prune with the keep-minimum guard → touch
     `~/backups/.<OFFSITE_NAME>-last-ok` → optional
     `curl -fsS "$OFFSITE_PING_URL"`;
   - verification picks `cryptcheck` when the remote supports hashes
     (Drive) and `check --download` when it doesn't (SMB, the RDS plan),
     so one script serves both;
   - `--dry-run` (passed through to rclone) for first-time setup, `--list`
     to print what Drive holds (and any duplicate names), `--no-prune` for
     when pruning runs under a separate account, and `--prune-only` for
     that separate account's run (§3.4 rule 8);
   - header comment in the same style as `backup.sh` (usage, cron line,
     environment overrides).
5. **Add `scripts/offsite-restore.sh`**, or a `--fetch <file>` mode on the
   sync script: download named backups from Drive into `~/backups` (or a
   given directory), so `scripts/restore.sh` can use them unchanged (§6).
6. **CI:** `.github/workflows/ci.yml` already runs `bash -n scripts/*.sh`,
   which covers the new scripts. Also consider adding `shellcheck` for
   `scripts/`. There's no Drive in CI, so test the rclone logic against a
   `local` remote pointed at a temp directory (a local remote has hashes,
   so it exercises the `cryptcheck` path):
   - copy, then run again and confirm nothing new is uploaded;
   - change a file on the "remote" and confirm `--immutable` fails;
   - add old-dated files and confirm the date-based prune removes them;
   - with too few dumps, confirm the keep-minimum guard stops the prune.
7. **Update docs** (§9).
8. **Deploy on `cossee-web-server`** using §5, then run the §8 checklist.

---

## 5. Operator instructions (after implementation)

Run on `cossee-web-server` as `deploy`, from `~/i-connects`, unless it says
"on your laptop".

### 5.1 One-time setup

**On your laptop** (it needs a browser), with the same rclone version or
newer:

```bash
rclone authorize "drive" "<client_id>" "<client_secret>"
# Log in as the backup account (Q1) and allow access. rclone prints a
# JSON token: copy the whole {...} line.
```

**On the server:**

```bash
sudo apt install rclone
rclone version                      # 1.60 or later

# Can the host reach Google? Needs to print an HTTP status line (a 4xx is fine).
curl -sSI https://www.googleapis.com/drive/v3/about | head -1

mkdir -p ~/.config/i-connects && chmod 700 ~/.config/i-connects
rclone config                       # create "gdrive" (drive, scope drive.file, your client ID;
                                    #   answer "n" to "Use web browser" and paste the token from the laptop;
                                    #   for a shared drive answer "y" to "Configure this as a Shared Drive")
                                    # then "gdrive-crypt" per §3.2
chmod 600 ~/.config/rclone/rclone.conf
rclone about gdrive:                # shows quota → login and token are right
rclone mkdir gdrive:i-connects-backups   # rclone must create it itself (drive.file, §3.2)

cat > ~/.config/i-connects/offsite-gdrive.env <<'EOF'
OFFSITE_REMOTE=gdrive-crypt:cossee-web-server
OFFSITE_NAME=gdrive
OFFSITE_RETENTION_DAYS=90
OFFSITE_KEEP_MIN=14
EOF
chmod 600 ~/.config/i-connects/offsite-gdrive.env
```

**Now store the two crypt passwords (the plaintext you typed into
`rclone config`, not the obscured values), the OAuth client ID and secret,
and the Google account login in the team secret store, next to `.env`.**
Don't skip this.

### 5.2 First run

```bash
E=~/.config/i-connects/offsite-gdrive.env
scripts/offsite-sync.sh --env $E --dry-run   # lists what would be uploaded; nothing is written
scripts/offsite-sync.sh --env $E             # first real run uploads the whole ~/backups history
scripts/offsite-sync.sh --env $E             # second run should upload nothing
scripts/offsite-sync.sh --env $E --list      # the files Drive now holds
```

To see the result the way a person would, open
[drive.google.com](https://drive.google.com) as the backup account (or
the shared drive). `i-connects-backups/cossee-web-server/` should contain
`db-*.dump` and `media-*.tar.gz` files, whose contents are ciphertext.

### 5.3 Wire into cron

`crontab -e` as `deploy`, and **replace** the existing Part K backup line
with the chained line from §3.6 (or §3.7 if RDS is also set up).

### 5.4 Prove it once: a restore from Drive

Do §6 with `--target` straight after setup. Until a file downloaded from
Drive has been restored successfully, don't count the off-site backup as
working.

---

## 6. Restore from Google Drive

For when the host is gone or `~/backups` is unusable. On a rebuilt host,
first follow `DEPLOYMENT.md` Parts B–H, then:

1. **The rebuilt host only needs outgoing HTTPS.** Unlike RDS, it doesn't
   need to be on the U of A network.
2. Install rclone, and recreate `rclone.conf` from the secret store: the
   **same** OAuth client ID and secret (it has to be the same client with
   `drive.file`, §3.2) plus the **same** crypt passwords. Get a fresh
   token with `rclone authorize "drive"` on a laptop, as in §5.1.
3. Find and fetch the backup you want:
   ```bash
   E=~/.config/i-connects/offsite-gdrive.env
   scripts/offsite-sync.sh --env $E --list
   scripts/offsite-restore.sh --env $E db-<stamp>.dump media-<stamp>.tar.gz   # → ~/backups/
   ```
   (Without the script:
   `rclone copy gdrive-crypt:cossee-web-server/db-<stamp>.dump ~/backups/`.)
4. Restore with the existing tool, unchanged:
   ```bash
   scripts/restore.sh --db ~/backups/db-<stamp>.dump --media ~/backups/media-<stamp>.tar.gz --yes
   ```
5. Continue with Part I (post-launch verification).

**Files downloaded by hand** from the Drive web page are still encrypted.
Decrypt them with the same `gdrive-crypt` settings: point a crypt remote at
the local folder and `rclone copy` out of it.

**A file pruned by mistake** is in the Drive trash for 30 days. Restore it
from the trash in the web page, or with
`rclone backend untrash gdrive:i-connects-backups`.

**Quarterly drill** (extends the Part K drill): fetch the newest dump
**from Drive**, not from `~/backups`, into a scratch directory, and restore
it with `--target drill_$(date +%Y%m)`. Also run
`rclone check --download` on that one file against the local copy. This
proves the token, the client ID and the encryption key all still work. Sign
in to the backup account in a browser at the same time, so a consumer
account (Q1 b) never looks inactive to Google.

---

## 7. Monitoring and failure modes

- **Success marker:** `~/backups/.gdrive-last-ok` is updated on every
  fully successful run. Check its age during routine checks
  (`stat -c %y ~/backups/.gdrive-last-ok`); anything older than about 26
  hours means the job is failing.
- **Dead-man's switch (recommended):** set `OFFSITE_PING_URL` to a check
  that alerts when no ping arrives within 26 hours. This also catches a
  failed `backup.sh`, because the chained sync then doesn't run.
- **Logs:** `~/backups/offsite-gdrive.log`, one timestamped line per step,
  in the same style as `backup.log`. Both files grow without limit. Add
  them to the log rotation in Part L, or have the scripts cap their size.
- **Quota:** check `rclone about gdrive:` monthly, or when the dead-man's
  switch fires. Trash counts.
- **People changes:** add these to the team's offboarding list.
  - Whoever owns the Google Cloud project and the OAuth client: move the
    project to someone who is staying before they leave.
  - If the backups are in a person's account (Q1 c) and they leave, move
    the files to a shared drive or a dedicated account **before** their
    account is closed; closing it deletes the files.
  - Whoever holds the 2-Step Verification recovery for the backup
    account: update it.

| Symptom in `offsite-gdrive.log` | Likely cause | Action |
| --- | --- | --- |
| `invalid_grant` / `Token has been expired or revoked` | the OAuth app is still in Testing (7-day expiry), the token was revoked, or the account was suspended | Publish the app (§4 step 3), then `rclone config reconnect gdrive:` with a new token from a laptop |
| `access_denied` / `admin_policy_enforced` | the Workspace admin blocked the app or the scope (Q4) | Ask the admin to allow the client ID |
| `403 rateLimitExceeded` / `userRateLimitExceeded` | too many API calls, usually on the shared rclone client (§3.5) | rclone retries by itself; if it keeps failing, use our own client ID |
| `403 storageQuotaExceeded` | the account or shared drive is full, trash included | Empty old trash, lower `OFFSITE_RETENTION_DAYS`, or get more storage (Q3) |
| `insufficientFilePermissions` when pruning | the nightly account is a Contributor (§3.4 rule 8) | Expected if pruning is meant to run under the second account: use `--no-prune` in cron |
| `directory not found` for `i-connects-backups` | the folder was made by hand, or by another client ID, and `drive.file` can't see it (§3.2) | Let rclone create the folder, or check `client_id` matches the one in the secret store |
| `--immutable` / `cryptcheck` mismatch | a file on Drive differs from the local file of the same name | **Investigate before doing anything else**: possible corruption or tampering. Don't delete either copy |
| duplicate file names reported | an interrupted upload, or someone uploading by hand | Compare with `rclone lsl`, keep the copy that passes `cryptcheck`, remove the other with `rclone dedupe` |
| prune skipped (keep-min guard) | fewer than `OFFSITE_KEEP_MIN` dumps on Drive | Expected in the first two weeks; after that, backups have been failing, so check `backup.log` |

---

## 8. Acceptance checklist

Off-site backups are "done" (per Part K) only when every box is ticked:

- [ ] Account and drive chosen, Q1–Q5 answers and data approval (Q2) recorded in §2.2
- [ ] Backups are in a shared drive or a dedicated account, **not** a person's account, or the risk is accepted in writing (Q1)
- [ ] Quota covers 90 days of copies plus 30 days of trash (Q3)
- [ ] OAuth client is Internal or **In production**, not Testing; client ID and secret in the secret store
- [ ] Crypt passwords and Google account login stored in the team secret store, off the host
- [ ] A run more than 7 days after setup still succeeds (proves the token doesn't expire)
- [ ] Second run uploads nothing; `--list` shows the full `~/backups` history
- [ ] Cron line from §3.6 (or §3.7) installed; the next morning `offsite-gdrive.log` shows a clean run
- [ ] Monitoring alert set up and tested once (skip one ping, confirm the alert fires)
- [ ] One file restored from Drive with `restore.sh --target` and spot-checked (§5.4)
- [ ] Quarterly drill (§6) and the offboarding items (§7) added to the team's lists

---

## 9. Changes to other docs

When this is implemented:

- **`DEPLOYMENT.md` Part K:** replace the "Off-host copy" paragraph with a
  short summary and a link here (and to `RDS_BACKUP.md` if both are used).
  Replace the cron line with the chained one from §3.6 or §3.7. Note that
  Drive's trash is the only undo, so VM disk snapshots are still
  recommended.
- **`DEPLOYMENT.md` Part L:** add `offsite-gdrive.log` to log rotation,
  and the staleness and quota checks to routine monitoring.
- **`DEPLOYMENT.md` Part M:** add "Google Drive OAuth token / client
  secret / crypt passwords" to the secret-rotation list, with the
  token-revocation steps from §3.3.
- **`DEPLOYMENT.md` Part F1:** add a line saying the SMTP Gmail account is
  **not** the backup account (Q1).
- **`RDS_BACKUP.md`:** if both are used, point §3.6 at §3.7 here, and add
  `--env` / `OFFSITE_NAME` to its script description.
- **`scripts/backup.sh` header:** mention that off-site copying is handled
  by `scripts/offsite-sync.sh`.
