# Off-site Backups to UAlberta Research Data Storage — plan

**Status: plan only, nothing here is implemented yet.** This closes the gap
named in `DEPLOYMENT.md` Part K ("Off-host copy"): the nightly
`scripts/backup.sh` writes to `~/backups` on `cossee-web-server`, and if that
host is lost, the backups go with it. This plan copies every new backup
to the University of Alberta **Research Data Storage Service** right after
the nightly backup finishes. IST calls the service **RDSS**; this document
says **RDS** for short.

Sources: the
[RDSS service page](https://www.ualberta.ca/en/information-services-and-technology/initiatives/research-data-storage-service.html)
(screen capture of 2026-09-24), the *RDSS Technical Details Infosheet*, the
*RDSS Solution Overview*, the *RDSS FAQ*, and *How to Map your Research
Drive to Finder in Mac*. §2 lists what those documents confirm and what
still needs an answer from IST.

Contents:

1. [What it does](#1-what-it-does)
2. [What the RDSS docs confirm, and what's still open](#2-what-the-rdss-docs-confirm-and-whats-still-open)
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
2. **New:** if the backup succeeded, cron runs `scripts/offsite-sync.sh`.
   It connects to the RDS drive over **SMB**
   (`smb.research-filer.ualberta.ca`) using `rclone`, without mounting the
   share, and:
   - uploads every finished backup file in `~/backups` that RDS doesn't
     have yet, **encrypted on the host before it leaves**;
   - downloads what it just uploaded and compares it with the local file;
   - removes RDS copies older than the off-site retention period (default
     90 days). It never deletes the newest copies, and it only prunes after
     a successful upload;
   - on success, updates a marker file and (optionally) pings a monitoring
     URL, so that a job that stops working gets noticed.
3. The job is **copy-only**: deleting or corrupting files in `~/backups`
   does not change what is on RDS. Old RDS copies are removed only by the
   prune step, which works from the date in each file's name.

Two copies, two retention periods:

| Where | Written by | Retention | Protects against |
| --- | --- | --- | --- |
| `~/backups` on `cossee-web-server` | `scripts/backup.sh` | 30 days (`RETENTION_DAYS`) | bad deploy or migration, accidental deletes; the fastest restore |
| RDS drive | `scripts/offsite-sync.sh` | 90 days (`OFFSITE_RETENTION_DAYS`) | losing the VM or its disk, host compromise, mistakes noticed late |

**RDSS itself is not backed up.** The FAQ says the hardware has drive
redundancy and can recover from ransomware-style activity, but "we are
unable to accommodate traditional backups." So the RDS copy is the only
off-site copy. If this plan's copies on RDS are deleted, they are gone.
That's why the safety rules in §3.4 matter, and it's why VM disk snapshots
(Part K) are still worth having.

---

## 2. What the RDSS docs confirm, and what's still open

### 2.1 Confirmed by the documentation

| Topic | What the docs say | What it means for this plan |
| --- | --- | --- |
| Protocol | SMB share at `smb://smb.research-filer.ualberta.ca/<drive name>` (Mac guide). Windows, Mac and Linux clients are supported (FAQ). | Use rclone's `smb` backend (§3.1). No kernel mount or root needed. |
| Other transfer path | "Integrated with Globus application for data transfers and sharing" (service page). | Fallback transport (§3.5) if an SMB account for automated use isn't possible. |
| Login | CCID, entered as `ad\CCID` with the CCID password (the same one used for UAlberta Gmail) (Mac guide). "CCID authentication required" (service page). | rclone `smb` settings: `domain = ad`, `user = <CCID>`. |
| Secondary CCIDs | "Secondary CCIDs cannot be used to access this service. Only primary CCIDs and Guest CCIDs…" Shared accounts are "not recommended" as drive Admin (FAQ). | **The main open problem** — see Q1. |
| Network reach | Reachable on the U of A network, or off campus via the U of A VPN. "Access and sharing is limited to within the U of A network." IST manages the drive's firewall (FAQ). | `cossee-web-server` is `142.244.87.193`, a U of A address, so no VPN should be needed. IST may still have to allow the host through the drive's firewall (Q2). |
| Drive name and path | Sent in the email that confirms the IT service request (FAQ). | Fills `OFFSITE_REMOTE` (§3.2). |
| Capacity and cost | Up to 50 TB per PI or research group at no charge. More than that needs a consultation (infosheet, FAQ). | Plenty of room. `~/backups` today is a few GB at most (`du -sh ~/backups`). |
| Who can request a drive | PIs, faculty conducting research, and research admins, using a primary or Guest CCID, through the U of A service portal. The request must include a **Research Storage Management Plan** and answers to data-type questions written with the VPR, CISO and Library RDM (service page, FAQ). | A COSSEE PI or research admin has to file the request (§4 step 1). |
| Users | IST adds and removes users through an IT service request. The drive Admin controls permissions on folders inside the drive (FAQ). | We can set up a folder only the backup account can write to, and control whether it can delete (§3.4). |
| Owner leaving | File a General IT Service Request to move ownership to someone who is staying (FAQ). | Add this to offboarding (§7). |
| Backups of RDSS | Drive redundancy and ransomware recovery, but **no traditional backups** (FAQ). | See the note at the end of §1. |
| Concurrent editing | "Simultaneous file editing is unavailable." | Not a problem: the job writes each file once, and a lock prevents two runs at the same time (§3.4). |

### 2.2 Still open — ask IST in the service request

| # | Question | Why it matters |
| --- | --- | --- |
| **Q1** | **Which account should the nightly job log in with?** Secondary CCIDs are refused, and the job runs at 03:00 with no person present. Ask, in this order of preference: (a) can IST add a departmental or functional primary account (not a person's CCID) as a **member** of the drive? The FAQ only discourages shared accounts as *Admin*. (b) If not, can we use a Globus guest collection shared to a Globus service identity (§3.5)? (c) If neither, a named team member's primary CCID, with the risks written down (below). | Option (c) means a person's CCID password, which also unlocks their email and every other U of A service, sits in a file on a **shared** server (`DEPLOYMENT.md` Part B). It also stops working when their password changes or they leave. |
| **Q2** | Does `142.244.87.193` need to be allowed through the drive's firewall, and is SMB (TCP 445) open from that subnet? | Otherwise the connection times out. The host's UFW already allows outgoing traffic (Part B4). |
| **Q3** | Are CCID passwords forced to expire on a schedule? Is MFA ever enforced on SMB logins? | Tells us how often the stored credential needs rotating, and whether it can work unattended at all. |
| **Q4** | For the service-request data-type questions: the dump contains personal information (names, emails, password hashes, session data), **encrypted on our side before upload**. Is RDSS approved for this data at that classification? | Answer honestly on the request form. Encryption is planned either way (§3.3), but it doesn't replace approval. |
| **Q5** | Can the drive Admin give the backup account **create-and-write but not delete** on the backup folder, with deletes handled by a second account? | Because RDSS keeps no backups, this is the only protection against a compromised host deleting the off-site copies (§3.4). |

**Record the answers in this file** (a short "Confirmed with IST on
<date>" table) before starting §4 step 2.

---

## 3. Design

### 3.1 Tool: `rclone` over SMB

Use [`rclone`](https://rclone.org) with its built-in **`smb` backend**
(available since rclone 1.60). Ubuntu 24.04's `apt install rclone` ships
1.60.x. Confirm with `rclone version`, and use the upstream `.deb` if it's
older. Reasons:

- **No mount.** rclone talks SMB directly as the `deploy` user. There's no
  `cifs-utils`, no `/etc/fstab` entry, no root and no stale mount to
  recover. This matters on a shared host.
- **Built-in client-side encryption** (a `crypt` remote wrapped around the
  SMB remote). See §3.3.
- **`copy --immutable` and `check --download`** cover upload and
  verification, and `rclone lsf` / `deletefile` cover retention.
- **Works with the fallbacks too.** If Q1 forces a different route, a
  Globus-mounted path or a local staging directory is also an rclone
  remote. The script doesn't change.

### 3.2 Configuration

Everything off-site is configured under `deploy`'s home directory, mode
`0600`. **Nothing goes in the app's `.env`.** The app never needs these
values, and `backup.sh` already reads nothing from `.env`.

```
~/.config/rclone/rclone.conf        # remotes: "rds" (SMB) and "rds-crypt" (encryption wrapper)
~/.config/i-connects/offsite.env    # settings for offsite-sync.sh (below)
```

`rclone.conf` (placeholders in `<>`; create it with `rclone config`, which
stores the passwords obscured):

```ini
[rds]
type = smb
host = smb.research-filer.ualberta.ca
domain = ad
user = <account from Q1>
pass = <rclone obscure output>

[rds-crypt]
type = crypt
remote = rds:<drive name from IST email>/i-connects-backups
filename_encryption = off          # keep db-<stamp>.dump names readable, so restores and pruning are simple
directory_name_encryption = false
password = <rclone obscure output>
password2 = <rclone obscure output>
```

With the `smb` backend, the first path element is the share, so
`rds:<drive name>/…` means share `<drive name>` on
`smb.research-filer.ualberta.ca`. That's the same address as
`smb://smb.research-filer.ualberta.ca/<drive name>` in the Mac guide.

`offsite.env` variables (script defaults in brackets):

| Variable | Meaning |
| --- | --- |
| `OFFSITE_REMOTE` | rclone destination, e.g. `rds-crypt:cossee-web-server` **(required, no default)** |
| `BACKUP_DIR` | local source [`~/backups`, same as `backup.sh`] |
| `OFFSITE_RETENTION_DAYS` | delete RDS copies older than this [`90`] |
| `OFFSITE_KEEP_MIN` | never prune below this many db dumps [`14`] |
| `OFFSITE_BWLIMIT` | optional rclone `--bwlimit`, so the job doesn't saturate the shared host's link [unset] |
| `OFFSITE_PING_URL` | optional URL to `curl` on success (Healthchecks.io-style dead-man's switch) [unset] |

### 3.3 Encryption and key custody

- Files are encrypted by rclone `crypt` **on the host, before upload**.
  RDS only ever holds ciphertext, so anyone else with access to the drive,
  or to the filer, can't read user data.
- File names stay readable (`filename_encryption = off`). They contain
  only timestamps, and readable names keep the pruning and restore steps
  simple.
- **Store the two crypt passwords and the RDS login outside the host**, in
  the same secret store as `.env` (Part K already says `.env` isn't in any
  backup on purpose). If the crypt passwords are lost, **every off-site
  backup is unreadable**. Keeping them only on the VM defeats the point,
  because this backup exists for the case where the VM is gone.
- Treat a leak of the crypt passwords the same as a `.env` leak (Part M):
  rotate them, then upload everything again under the new key into a new
  folder. Delete the old folder once the new copies pass verification.
  Rotating the RDS *login* password (Q3) only means updating `pass` with
  `rclone config`; the files don't change.

### 3.4 Safety rules the script must follow

Because RDSS keeps no backups of its own (§1), the copies this job writes
are the only off-site copies. The rules below exist to keep them safe.

1. **Never propagate deletions.** Use `rclone copy`, never `rclone sync`.
   With `sync`, an emptied or ransomwared `~/backups` would empty RDS on
   the next run.
2. **Don't overwrite on RDS.** Pass `--immutable`, so a file that already
   exists on RDS but differs is reported as an error, not replaced. Backup
   files never change after `backup.sh` renames them into place, so any
   mismatch means something is wrong.
3. **Upload only finished files.** Filter to `db-*.dump`,
   `media-*.tar.gz` and the legacy `db-*.sql.gz`. Exclude `*.tmp`, because
   `backup.sh` writes to `.tmp` and renames when done. Also exclude
   `backup.log`, `offsite.log` and the dotfiles. Logs can contain paths and
   hostnames and aren't needed for a restore.
4. **Verify by reading the files back.** The SMB backend has no
   server-side checksums, so `rclone check` would only compare sizes, and
   `cryptcheck` doesn't work without checksums. Instead, run
   `rclone check --download --files-from <this run's uploads>`. It
   downloads each new file through the crypt layer and compares it byte for
   byte with the local copy. Tonight's files are small, so this is cheap.
   If it fails, exit non-zero and don't prune.
5. **Prune by the date in the file name, not the file's modified time.**
   List the remote with `rclone lsf`, parse the timestamp in
   `db-YYYY-MM-DDTHHMMSS.dump` and `media-…tar.gz`, and delete with
   `rclone deletefile` anything older than `OFFSITE_RETENTION_DAYS`.
   Pruning runs only after steps 1–4 succeed, and only if at least
   `OFFSITE_KEEP_MIN` db dumps would remain. This avoids depending on SMB
   preserving modified times, and it keeps a run of failed backups from
   causing the prune step to delete the last good copies.
6. **Allow one run at a time.** Wrap the script in `flock` on
   `~/backups/.offsite.lock`, so a slow upload can't overlap the next
   night's run.
7. **Keep the same shape as `backup.sh`:** `set -euo pipefail`, the same
   `log()`/`fail()` format with timestamped lines, and exit non-zero on any
   failure so cron and monitoring can see it.
8. **If Q5 is possible, use separate accounts for writing and deleting.**
   The nightly account gets create-and-write but not delete on
   `i-connects-backups/`. Pruning (step 5) then runs under a second
   credential that isn't stored in the nightly job's config, for example
   monthly and by hand. A compromised host could then add junk but couldn't
   delete history. If Q5 isn't possible, one account does both, and the
   drive's ransomware recovery (FAQ) is the only protection.

### 3.5 Fallback: Globus

Use this only if Q1 rules out SMB for an automated login. RDSS is
"integrated with Globus." The route would be:

- The drive Admin creates a **Globus guest collection** on the
  `i-connects-backups` folder and grants write access to a **Globus client
  (service) identity** registered for this job. No CCID password is stored
  on the host.
- The job first encrypts into a local staging directory (the same rclone
  `crypt` remote pointed at `~/backups/.offsite-staging`), then runs
  `globus transfer --sync-level exists` into the guest collection, which
  gives the same "copy what's missing, never delete" behaviour.
- For verification, rely on Globus transfer checksums (`--verify-checksum`,
  the default).
- Pruning uses `globus rm` with the same date and keep-minimum rules.

This needs IST to confirm that guest collections and service identities
are allowed on RDSS endpoints. It has more moving parts than SMB, so it's
the fallback, not the default.

### 3.6 Scheduling

Chain the new script after the existing backup in the same cron entry, so
the off-site copy happens right after each successful backup:

```cron
0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1 && scripts/offsite-sync.sh >> ~/backups/offsite.log 2>&1
```

- `&&` means a failed backup doesn't trigger an upload. That run's
  failure shows up in `backup.log` and in monitoring (§7).
- Missed runs catch up: the script uploads **every** local file RDS lacks,
  not just tonight's. If one night's upload fails, for example because the
  filer is down for maintenance, the next successful run uploads both
  nights' files. The local 30-day retention is the window for noticing and
  fixing a broken off-site job before backups start disappearing from the
  host without an off-site copy.

---

## 4. Implementation steps

Do these in order. Each code step can be its own PR.

1. **Request the drive.** A COSSEE PI, faculty member or research admin
   submits the RDSS "new service" IT service request through the
   [U of A service portal](https://www.ualberta.ca/en/information-services-and-technology/initiatives/research-data-storage-service.html).
   - Attach a Research Storage Management Plan. The volume needed is under
     100 GB, which leaves room to grow.
   - Answer the data-type questions accurately: personal information,
     encrypted on our side (Q4).
   - Ask Q1–Q5 in the same request.
   - The drive name arrives by email.
2. **Record the answers** in §2.2 of this file, and settle which option
   Q1 leads to (SMB account or Globus).
3. **Add `scripts/offsite-sync.sh`** following §3.2–3.4 (or §3.5):
   - load `~/.config/i-connects/offsite.env`; fail clearly if
     `OFFSITE_REMOTE` is unset, `rclone` is missing, or its version is
     below 1.60;
   - `flock` → `rclone copy --immutable` with the include/exclude filters
     → `rclone check --download` on this run's uploads → date-based prune
     with the keep-minimum guard → touch `~/backups/.offsite-last-ok` →
     optional `curl -fsS "$OFFSITE_PING_URL"`;
   - `--dry-run` (passed through to rclone) for first-time setup, `--list`
     to print what RDS holds, and `--no-prune` for when pruning runs under
     a separate account (§3.4 rule 8);
   - header comment in the same style as `backup.sh` (usage, cron line,
     environment overrides).
4. **Add `scripts/offsite-restore.sh`**, or a `--fetch <file>` mode on the
   sync script: download named backups from RDS into `~/backups` (or a
   given directory), so `scripts/restore.sh` can use them unchanged (§6).
5. **CI:** `.github/workflows/ci.yml` already runs `bash -n scripts/*.sh`,
   which covers the new scripts. Also consider adding `shellcheck` for
   `scripts/`. There's no RDS in CI, so test the rclone logic against a
   `local` remote pointed at a temp directory:
   - copy, then run again and confirm nothing new is uploaded;
   - change a file on the "remote" and confirm `--immutable` fails;
   - add old-dated files and confirm the date-based prune removes them;
   - with too few dumps, confirm the keep-minimum guard stops the prune.
6. **Update docs** (§9).
7. **Deploy on `cossee-web-server`** using §5, then run the §8 checklist.

---

## 5. Operator instructions (after implementation)

Run on `cossee-web-server` as `deploy`, from `~/i-connects`.

### 5.1 One-time setup

```bash
sudo apt install rclone
rclone version                      # must be 1.60 or later for the smb backend

# Can the host reach the filer? (Q2) Needs to print "succeeded" / "open".
nc -zv smb.research-filer.ualberta.ca 445

mkdir -p ~/.config/i-connects && chmod 700 ~/.config/i-connects
rclone config                       # create "rds" (smb, domain ad) then "rds-crypt" per §3.2
chmod 600 ~/.config/rclone/rclone.conf
rclone lsd rds:<drive name>         # lists the drive's top-level folders → login and share are right
rclone mkdir rds:<drive name>/i-connects-backups

cat > ~/.config/i-connects/offsite.env <<'EOF'
OFFSITE_REMOTE=rds-crypt:cossee-web-server
OFFSITE_RETENTION_DAYS=90
OFFSITE_KEEP_MIN=14
EOF
chmod 600 ~/.config/i-connects/offsite.env
```

**Now store the two crypt passwords (the plaintext you typed into
`rclone config`, not the obscured values) and the RDS login in the team
secret store, next to `.env`.** Don't skip this.

### 5.2 First run

```bash
scripts/offsite-sync.sh --dry-run   # lists what would be uploaded; nothing is written
scripts/offsite-sync.sh             # first real run uploads the whole ~/backups history
scripts/offsite-sync.sh             # second run should upload nothing
scripts/offsite-sync.sh --list      # the files RDS now holds
```

To see the result the way a person would, map the drive from a laptop on
campus or on the VPN, following the IST Windows or Mac guide
(`smb://smb.research-filer.ualberta.ca/<drive name>`, log in as
`ad\<CCID>`). `i-connects-backups/cossee-web-server/` should contain
`db-*.dump` and `media-*.tar.gz` files, whose contents are ciphertext.

### 5.3 Wire into cron

`crontab -e` as `deploy`, and **replace** the existing Part K backup line
with the chained line from §3.6.

### 5.4 Prove it once: a restore from RDS

Do §6 with `--target` straight after setup. Until a file downloaded from
RDS has been restored successfully, don't count the off-site backup as
working.

---

## 6. Restore from RDS

For when the host is gone or `~/backups` is unusable. On a rebuilt host,
first follow `DEPLOYMENT.md` Parts B–H, then:

1. **The rebuilt host must be able to reach the filer.** Either it's on
   the U of A network and allowed through the drive's firewall (Q2; if its
   IP has changed, file an IT request), or it uses the U of A VPN.
2. Install rclone, and recreate `rclone.conf` from the secret store: the
   RDS login plus the **same** crypt passwords.
3. Find and fetch the backup you want:
   ```bash
   scripts/offsite-sync.sh --list
   scripts/offsite-restore.sh db-<stamp>.dump media-<stamp>.tar.gz   # → ~/backups/
   ```
   (Without the script:
   `rclone copy rds-crypt:cossee-web-server/db-<stamp>.dump ~/backups/`.)
4. Restore with the existing tool, unchanged:
   ```bash
   scripts/restore.sh --db ~/backups/db-<stamp>.dump --media ~/backups/media-<stamp>.tar.gz --yes
   ```
5. Continue with Part I (post-launch verification).

**Files copied off RDS by hand** (for example dragged out through Finder)
are still encrypted. Decrypt them with the same `rds-crypt` settings: point
a crypt remote at the local folder and `rclone copy` out of it.

**Quarterly drill** (extends the Part K drill): fetch the newest dump
**from RDS**, not from `~/backups`, into a scratch directory, and restore it
with `--target drill_$(date +%Y%m)`. This proves the network path, the
login and the encryption key all still work.

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
- **People changes:** add both of these to the team's offboarding list.
  - If the drive owner leaves, file a General IT Service Request to move
    ownership (FAQ).
  - If the backup login is a person's CCID (Q1 option c) and they leave,
    or their password changes, update `rclone.conf` *before* the old
    credential stops working.

| Symptom in `offsite.log` | Likely cause | Action |
| --- | --- | --- |
| `STATUS_LOGON_FAILURE` / auth error | the CCID password changed or expired (Q3), or the account was removed from the drive | Update `pass` with `rclone config`, or file an IT request to re-add the account |
| `STATUS_ACCESS_DENIED` when writing | folder permissions on the drive changed | Drive Admin fixes folder permissions |
| connection timed out / refused on 445 | filer maintenance, or the host is no longer allowed through the drive firewall (Q2) | Retry next night; if it persists, contact IST. Local backups keep 30 days |
| quota / disk full | over the allocation | Lower `OFFSITE_RETENTION_DAYS`, or file an "increase storage" request |
| `--immutable` / check mismatch | a file on RDS differs from the local file of the same name | **Investigate before doing anything else**: possible corruption or tampering. Don't delete either copy |
| prune skipped (keep-min guard) | fewer than `OFFSITE_KEEP_MIN` dumps on RDS | Expected in the first two weeks; after that, backups have been failing, so check `backup.log` |

---

## 8. Acceptance checklist

Off-site backups are "done" (per Part K) only when every box is ticked:

- [ ] RDSS drive granted; drive name, Q1–Q5 answers and data-type approval (Q4) recorded in §2.2
- [ ] Backup login is a functional account or a Globus identity, **not** a person's CCID, or the risk is accepted in writing (Q1)
- [ ] Crypt passwords and RDS login stored in the team secret store, off the host
- [ ] `nc -zv smb.research-filer.ualberta.ca 445` succeeds from `cossee-web-server`
- [ ] Second run uploads nothing; `--list` shows the full `~/backups` history
- [ ] Cron line from §3.6 installed; the next morning `offsite.log` shows a clean run
- [ ] Monitoring alert set up and tested once (skip one ping, confirm the alert fires)
- [ ] One file restored from RDS with `restore.sh --target` and spot-checked (§5.4)
- [ ] Quarterly drill (§6) and the offboarding items (§7) added to the team's lists

---

## 9. Changes to other docs

When this is implemented:

- **`DEPLOYMENT.md` Part K:** replace the "Off-host copy" paragraph with a
  short summary and a link here. Replace the cron line with the chained one
  from §3.6. Note that RDSS keeps no backups of its own, so VM disk
  snapshots are still recommended.
- **`DEPLOYMENT.md` Part L:** add `offsite.log` to log rotation and the
  staleness check to routine monitoring.
- **`DEPLOYMENT.md` Part M:** add "RDS login / crypt passwords" to the
  secret-rotation list.
- **`scripts/backup.sh` header:** mention that off-site copying is handled
  by `scripts/offsite-sync.sh`.
