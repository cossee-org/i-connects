# Production Deployment — i-connects

End-to-end instructions for a secure production launch on the dedicated
server, over SSH. Domain: **`i-connects.cossee.org`**. Target host:
`cossee-web-server`, Ubuntu 24.04.4 LTS (`noble`), kernel 6.8, x86_64.

Stack: Docker Compose (Django/gunicorn + Postgres 16 + Redis 7 + Celery +
Celery Beat), fronted by the host's shared Caddy 2 for automatic Let's
Encrypt TLS (Part G). `DEMO_DEPLOYMENT.md` is retired; this guide is the one
that matches the committed compose files.

This repo already contains a full audit at `SECURITY_AUDIT.md`
(2026-07-08). **Part A below applies every Critical/High finding from that
audit that isn't yet fixed in the tree.** Do not skip Part A — several of
these are "silently serves debug tracebacks / hardcoded secret key to the
public internet" class bugs, not theoretical hardening.

---

## Contents

- [Part A — Required fixes before the first production image is built](#part-a)
- [Part B — Server hardening (Ubuntu 24.04)](#part-b)
- [Part C — Install Docker](#part-c)
- [Part D — DNS](#part-d)
- [Part E — Get the code onto the server](#part-e)
- [Part F — Configure `.env`](#part-f)
- [Part G — Reverse proxy (shared host Caddy)](#part-g)
- [Part H — Launch](#part-h)
- [Part I — Post-launch verification](#part-i)
- [Part J — Day-2 operations](#part-j)
- [Part K — Backups & disaster recovery](#part-k)
- [Part L — Monitoring & log rotation](#part-l)
- [Part M — Secret rotation / incident response](#part-m)
- [Part N — Troubleshooting](#part-n)
- [Part O — Tear-down](#part-o)

---

<a id="part-a"></a>
## Part A — Required fixes before the first production image is built

Apply these on a branch, review, merge to `main` — **do this before Part E**
(pulling the code onto the server). Every item cites the finding it closes.

### A1. Add `.dockerignore` (closes SECURITY_AUDIT C1)

The Dockerfile does `COPY . .`. Without a `.dockerignore`, a stray `.env`,
`.git` history, or `node_modules` in the build context gets baked into an
image layer — recoverable by anyone who can pull or `docker save` the
image, even after a later layer deletes the file.

Create `.dockerignore` at the repo root:

```
.env
.env.*
!.env.example
.git
.github
node_modules
__pycache__
*.pyc
media/
staticfiles/
*.md
!README.md
.pytest_cache
htmlcov
.coverage
```

### A2. Make the settings module fail closed (closes SECURITY_AUDIT C2)

Today `Dockerfile` defaults `DJANGO_SETTINGS_MODULE=config.settings.dev`.
If the `.env` file is ever missing that override — a typo, a missed copy, a
future migration to an orchestrator that drops the env var — the container
silently serves `dev.py`: `DEBUG=True` (full tracebacks with source, locals,
SQL, and environment to the public internet), `ALLOWED_HOSTS=["*"]`, and a
hardcoded, publicly-known `SECRET_KEY`.

In `Dockerfile`, change the default to the safe (fail-loud, not
fail-open) direction:

```diff
 ENV PYTHONDONTWRITEBYTECODE=1 \
     PYTHONUNBUFFERED=1 \
     PIP_NO_CACHE_DIR=1 \
     PIP_DISABLE_PIP_VERSION_CHECK=1 \
-    DJANGO_SETTINGS_MODULE=config.settings.dev
+    DJANGO_SETTINGS_MODULE=config.settings.prod
```

`docker-compose.yml` (plain, dev) already overrides this via `.env`, so
local development is unaffected as long as your dev `.env` still sets
`DJANGO_SETTINGS_MODULE=config.settings.dev`. Production `.env` (Part F)
now matches the image default instead of depending on it.

### A3. Run the container as a non-root user (closes SECURITY_AUDIT H2)

`Dockerfile` has no `USER` directive today, so gunicorn runs as root. A
remote-code-execution bug in the app or any dependency then hands an
attacker root inside the container. Add, in the final stage, after
`COPY . .` and before `EXPOSE`:

```diff
 COPY . .

 # Bring in the Tailwind-compiled CSS from the css-builder stage.
 COPY --from=css-builder /src/static/css/output.css ./static/css/output.css

+RUN useradd -m -u 1000 appuser \
+ && mkdir -p /app/staticfiles /app/media \
+ && chown -R appuser:appuser /app
+USER appuser
+
 EXPOSE 8000
```

### A4. Bump `Django`, `psycopg[binary]` and the Node build stage (closes H3/H4)

```diff
-Django==5.2
+Django==5.2.*        # pin to the latest 5.2.x patch at merge time
 ...
-psycopg[binary]==3.2.3
+psycopg[binary]==3.2.*   # pin to the latest 3.2.x patch at merge time
```

```diff
-FROM node:20-bookworm-slim AS css-builder
+FROM node:22-bookworm-slim AS css-builder
```

Run `pytest -q` after bumping — no code changes are expected, this is a
patch-level bump only.

### A5. Require verified email before invitations/login trust it (closes H1) — settings change alone is NOT sufficient

`config/settings/base.py` sets `ACCOUNT_EMAIL_VERIFICATION = "optional"`,
and `AcceptOrDeclineInviteView` authorizes purely by string-matching
`request.user.email`. Anyone can sign up with an email they don't own and
hijack an invitation sent to that address before the real owner ever signs
up.

```diff
-ACCOUNT_EMAIL_VERIFICATION = "optional"
+ACCOUNT_EMAIL_VERIFICATION = "mandatory"
+ACCOUNT_EMAIL_REQUIRED = True
```

(`ACCOUNT_EMAIL_REQUIRED` is a companion setting allauth's own settings
check requires alongside `mandatory` verification — omitting it is a
CRITICAL-level boot failure at `manage.py migrate`, confirmed live during
this deployment.)

**Important — confirmed live on `cossee-web-server`: this settings change
alone does not close H1.** `ACCOUNT_EMAIL_VERIFICATION` is an **allauth**
setting; it only gates allauth's own account views/adapter flow, which
this app uses solely for ORCID social login (per the docstring atop
`apps/accounts/views.py`). The app's actual email/password signup and
login are hand-rolled views in that same file — `SignupView.form_valid`
calls `login(self.request, user, ...)` directly right after
`form.save()`, and `LoginView.form_valid` does the same, with **no check
of email-verification status anywhere in either path**. A real signup with
an unconfirmed address still gets a fully authenticated session; the
setting change above does nothing to stop it. `AcceptOrDeclineInviteView`
is unchanged and still trusts `request.user.email` outright.

Actually closing H1 needs an app-code change to `apps/accounts/views.py`
(and likely `apps/accounts/forms.py`/templates for the confirmation-sent
and confirm-your-email screens) — at minimum, per the audit's own
suggested fix, gating `AcceptOrDeclineInviteView` and password-reset trust
behind a real `email_verified` check, and/or not fully authenticating a
session until that check passes. That's a genuine feature change with UX
implications (this app currently auto-logs a user in immediately on
signup, by design), not a one-line deployment-config fix — treat the
settings change above as necessary-but-not-sufficient, track the actual
code fix separately, and don't rely on `ACCOUNT_EMAIL_VERIFICATION` for
security guarantees on the email/password path until it lands.

The settings change is still worth making now regardless, since it's
required for the ORCID login path to behave correctly and is a
prerequisite either way. This requires outbound email to work (Part F,
SMTP/Resend/SES) for the paths that do use it — verify signup →
confirmation email → login works end-to-end in staging.

### A6. Guard `seed_demo` against production databases (closes M3)

`apps/core/management/commands/seed_demo.py` creates real accounts with the
publicly-documented password `demopass` and has no runtime guard beyond a
docstring. Add one:

```diff
 class Command(BaseCommand):
     def handle(self, *args, **options):
+        from django.conf import settings
+        if not settings.DEBUG:
+            raise CommandError(
+                "seed_demo is dev/demo-only and refuses to run when DEBUG=False."
+            )
```

(`CommandError` import from `django.core.management.base`.) This also means
`seed_demo` is unavailable once Part A2 takes effect in production, which is
the intent — never run it against `i-connects.cossee.org`.

### A7. Fix `ports: []` / `volumes: []` not actually closing db/redis or dropping the dev bind-mount, and add Redis auth (closes M4)

**Update:** `docker-compose.prod.yml` now applies all three fixes below
(`!reset`/`!override` on `ports`/`volumes`, plus Redis auth) and drops its
own `caddy:` service per Part G1 — this used to be a diff you had to apply
by hand and, on at least one deployment, never was; `docker compose config`
against the old file confirmed db/redis were still published to `0.0.0.0`
and web/celery/celery-beat were still bind-mounting the host source tree
into `/app` despite the `ports: []` / `volumes: []` overrides being
present. If you deployed before this fix landed, treat any data that isn't
in the `postgres_data` volume as unrecoverable and re-check Part K.

Three separate bugs, same root cause.

**Bug 1 — `docker-compose.prod.yml`'s `ports: []` is a no-op.** Docker
Compose merges `ports:` (and `expose`, `volumes`, a few other list fields)
across `-f` files by **union, not replacement** — an empty list in an
override contributes zero *additional* entries, it does not clear what the
base `docker-compose.yml` already published (`"5432:5432"` /
`"6379:6379"`). Confirmed live: `docker compose -f docker-compose.yml -f
docker-compose.prod.yml up -d` still leaves both published to the public
internet, even though the override was applied (verify with `docker ps
--format '{{.Names}}\t{{.Ports}}'`). Worth stating plainly: this also means
UFW (Part B4) is **not** protecting these ports even if it's configured
correctly — Docker inserts its own `iptables`/`DOCKER-USER` rules ahead of
UFW's, so a published container port bypasses the host firewall entirely.

Fix it with Compose's `!reset` tag, which explicitly replaces a field
instead of merging it (needs Compose v2.24+; check with `docker compose
version` — anything from `get.docker.com` on Ubuntu 24.04 in 2026
qualifies):

```diff
 db:
-  ports: []
+  ports: !reset []
   restart: unless-stopped
 ...
 redis:
-  ports: []
+  ports: !reset []
   restart: unless-stopped
```

If you already brought the stack up before applying this, the running
containers keep their old (exposed) config until recreated — after editing,
re-run `up -d` and confirm with `docker ps` that neither shows a
`0.0.0.0:...->...` mapping. If you need to stop the exposure immediately
before you can edit and rebuild, `docker stop <db-container>
<redis-container>` first.

**Bug 2 — the same no-op applies to `volumes: []` on `web`/`celery`/
`celery-beat`.** These override blocks were meant to drop the dev-only
`.:/app` bind mount from `docker-compose.yml` (so production runs the
image `COPY . .` built into it, not whatever happens to be sitting in the
host checkout). Because `volumes` merges by the same union rule as `ports`,
`volumes: []` left the bind mount in place — production was silently
running off the live host directory the whole time. This doesn't affect
the database (`db`'s named `postgres_data` volume is untouched by any of
this), but it does mean a code change on disk, a bad `git` operation, or
the host directory going away could have changed production's actual
running code without a rebuild, or with confusing effect after one. Fix it
the same way:

```diff
 web:
   ...
-  volumes: []
+  volumes: !reset []
   ...
-  ports: []
+  ports: !override
+    - "127.0.0.1:8000:8000"

 celery:
-  volumes: []
+  volumes: !reset []
   restart: unless-stopped

 celery-beat:
-  volumes: []
+  volumes: !reset []
   restart: unless-stopped
```

(`web`'s `ports` uses `!override` here rather than `!reset []` because it
also needs new content — the loopback-only binding from Part G1 — not just
an empty result.)

**Bug 3 — no Redis password.** Even fully unpublished (internal-network
only), any compromised container on the same Docker network (web, celery)
has full unauthenticated read/write to the broker and result backend. Add a
password:

```diff
 redis:
   ports: !reset []
   restart: unless-stopped
+  command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
```

You'll set `REDIS_PASSWORD` and matching `REDIS_URL` /
`CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` in `.env` — see Part F.

### A8. Broaden `.gitignore` for env files (closes M5)

```diff
-.env
-.env.local
-.env.*.local
+.env*
+!.env.example
```

### A9. CSV formula-injection fix (closes M1) — recommended before real user data flows through exports

`apps/contributions/views.py` writes user-controlled cells (`project`,
`user_name`, `task`) into CSV without sanitizing a leading `=+-@`. Prefix
any such cell with `'` before writing. Not a deployment-config item, but
flag it to whoever owns the app code before contribution exports are handed
to PIs with untrusted project membership.

### A10. Verify the fixes

```bash
git diff --stat        # confirm the above files changed
pytest -q              # 27+ tests still pass
docker build -t iconnects:preflight .
docker run --rm iconnects:preflight id     # expect uid=1000(appuser), not 0/root
```

Commit, push, merge to `main`. Only pull `main` onto the server (Part E)
once this lands.

---

<a id="part-b"></a>
## Part B — Server hardening (Ubuntu 24.04)

SSH into `cossee-web-server` as an account with sudo. Confirm the OS first:

```bash
cat /etc/os-release && uname -a
```

### B1. Patch and enable unattended security updates

```bash
sudo apt-get update && sudo apt-get -y full-upgrade
sudo apt-get install -y unattended-upgrades apt-listchanges
sudo dpkg-reconfigure -plow unattended-upgrades   # enable when prompted
```

### B2. Create accounts (shared server — one per person, plus a deploy account)

`cossee-web-server` is shared: other people besides whoever runs this guide
need to keep logging in, so this isn't a single-account setup. Give **every
person their own Linux account** (not a shared login) so `last`/`sudo` logs
stay attributable, plus one dedicated account for running the app:

```bash
# once per human who needs access
sudo adduser <username>
sudo usermod -aG sudo <username>          # only if they need sudo

# once, for running Docker/the app itself
sudo adduser deploy
sudo usermod -aG sudo deploy
```

If someone already has a key you want carried over from the account you're
currently using, copy it into their new account:

```bash
sudo rsync --archive --chown=<username>:<username> ~/.ssh /home/<username>
```

Otherwise have each person send you their own SSH public key and add it
directly:

```bash
sudo mkdir -p /home/<username>/.ssh
sudo nano /home/<username>/.ssh/authorized_keys   # paste their public key, one per line
sudo chown -R <username>:<username> /home/<username>/.ssh
sudo chmod 700 /home/<username>/.ssh
sudo chmod 600 /home/<username>/.ssh/authorized_keys
```

Do the rest of this guide as `deploy` (or your own personal account with
sudo), not `root`.

### B3. SSH hardening

This server keeps **both SSH key and password login enabled** for people who
need it — that's a deliberate call for this deployment, not the more
locked-down "keys only" setup some guides default to. Root login is still
turned off, since every human here has (or should have) their own named
account with `sudo` instead:

```
PermitRootLogin no
```

Edit `/etc/ssh/sshd_config` (or drop a file in `/etc/ssh/sshd_config.d/`)
with just that line, then `sudo systemctl restart ssh`. **Verify a new SSH
session still logs in before closing your current session** — a typo here
locks you out.

Leaving password auth on does mean the server is reachable by anyone who
guesses a valid username/password, so the compensating controls below are
not optional — they're what makes "passwords stay allowed" an acceptable
trade rather than an open door:

- **Fail2ban (B5) is now load-bearing, not optional.** It's what turns
  repeated bad-password attempts from a given IP into an automatic ban —
  confirm it's actually running (`sudo fail2ban-client status sshd`) before
  you consider this step done.
- **Enforce a real password policy** so "password login stays on" doesn't
  mean "weak passwords stay on":
  ```bash
  sudo apt-get install -y libpam-pwquality
  ```
  Then in `/etc/pam.d/common-password`, add `minlen=14 retry=3` to the
  `pam_pwquality.so` line's arguments (or create it if the package didn't
  add one). Every account created after this point is held to it; existing
  accounts should rotate to a compliant password.
- **Ask everyone with an account to prefer their SSH key day-to-day** and
  treat their password as a fallback, not their normal login — the key
  can't be brute-forced over the network the way a password can.
- **Lower the SSH auth budget** to slow down credential-stuffing without
  disabling passwords outright — add to the same `sshd_config` drop-in as
  above:
  ```
  MaxAuthTries 3
  LoginGraceTime 20
  ```
- Revisit this trade-off periodically — if the set of people needing access
  stabilizes and everyone's comfortable with keys, switching to
  `PasswordAuthentication no` later (per the stricter variant of this step)
  removes the remaining attack surface entirely without removing anyone's
  access, since their keys already work.

### B4. Firewall (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH        # or: sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

No other port should be open. Postgres (`5432`) and Redis (`6379`) should
never need a UFW rule — `docker-compose.prod.yml` is meant to keep them off
the host network entirely. **Don't treat UFW as a backstop for that,
though**: Docker publishes container ports by inserting its own
`iptables`/`DOCKER-USER` rules ahead of UFW's, so a container that ends up
published to `0.0.0.0` (see Part A7's merge-bug fix) is reachable from the
internet regardless of what UFW says. UFW here is for the host's own
listening processes (SSH) — it is not a safety net for Docker port
publishing.

### B5. Fail2ban for SSH brute-force protection

```bash
sudo apt-get install -y fail2ban
sudo systemctl enable --now fail2ban
```

Default `sshd` jail is enabled out of the box on Ubuntu's fail2ban package;
confirm with `sudo fail2ban-client status sshd`.

### B6. Time sync (needed for valid TLS handshakes and cron accuracy)

```bash
timedatectl set-ntp true
timedatectl status   # "System clock synchronized: yes"
```

---

<a id="part-c"></a>
## Part C — Install Docker

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
```

Log out and back in (or `newgrp docker`) so the group membership takes
effect, then confirm:

```bash
docker --version
docker compose version
```

---

<a id="part-d"></a>
## Part D — DNS

`cossee.org` is registered at Namecheap. Add an **A** record there: host
`i-connects`, value the server's public IP —
`142.244.87.193` for `cossee-web-server` as of this writing; reconfirm with
`curl -s https://api.ipify.org` run on the server itself, since that's the
authoritative source, not this document. Skip the AAAA/IPv6 record unless
`curl -6 -s https://api64.ipify.org` on the server actually returns an
address — most hosts here don't have IPv6 configured, and a stale AAAA
record pointing nowhere breaks IPv6-preferring clients even though the A
record is fine.

```
i-connects.cossee.org.   A   142.244.87.193
```

Verify propagation from the server and from an outside network before
proceeding — automatic TLS issuance in Part G fails, and can hit Let's
Encrypt rate limits on repeated failed attempts, if this isn't resolvable
yet:

```bash
dig +short i-connects.cossee.org
curl -s https://api.ipify.org   # confirm it matches this server's public IP
```

---

<a id="part-e"></a>
## Part E — Get the code onto the server

The repo is private. Use a fine-grained, read-only, repo-scoped GitHub
Personal Access Token (or a deploy key if you prefer key-based auth):

```bash
git clone https://github.com/cossee-org/i-connects.git ~/i-connects
cd ~/i-connects
git checkout main
git log -1 --oneline    # confirm Part A's commit is present
```

---

<a id="part-f"></a>
## Part F — Configure `.env`

```bash
cd ~/i-connects
cp .env.example .env
```

Generate strong secrets — a different one for each, never reused between
`DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, and `REDIS_PASSWORD`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Edit `.env`:

```ini
# ---- Django ----
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_SECRET_KEY=<paste a generated hex>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=i-connects.cossee.org
SECURE_SSL_REDIRECT=True

# ---- Postgres ----
POSTGRES_DB=iconnects
POSTGRES_USER=iconnects
POSTGRES_PASSWORD=<paste a different generated hex>
DATABASE_URL=postgres://iconnects:<same password>@db:5432/iconnects

# ---- Redis / Celery (after applying A7) ----
REDIS_PASSWORD=<paste a third generated hex>
REDIS_URL=redis://:<same password>@redis:6379/0
CELERY_BROKER_URL=redis://:<same password>@redis:6379/0
CELERY_RESULT_BACKEND=redis://:<same password>@redis:6379/0

# ---- Email (pick ONE backend) ----
EMAIL_BACKEND_CHOICE=smtp
DEFAULT_FROM_EMAIL=i-connects <your-account@gmail.com>

# Absolute base URL (scheme + host, no trailing slash). Required — links in
# emails sent from Celery tasks (invitations, join requests, digests) are
# built from this, since those tasks have no HTTP request to derive a
# domain from. Left unset, those links silently degrade to a bare relative
# path (e.g. "/invites/<code>/"), which email clients then mangle into a
# broken "http:///invites/<code>/" link.
SITE_URL=https://i-connects.cossee.org

# smtp, via a Gmail account — see "Setting up Gmail SMTP" below for how to
# get EMAIL_HOST_PASSWORD (it is NOT the account's normal login password):
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=<your-account@gmail.com>
EMAIL_HOST_PASSWORD=<16-character App Password, no spaces>
EMAIL_USE_TLS=True

# resend (use instead of the smtp block above; set EMAIL_BACKEND_CHOICE=resend):
# RESEND_API_KEY=<key>

# ses (use instead; set EMAIL_BACKEND_CHOICE=ses is not a supported choice today —
# django-ses is installed but not wired into EMAIL_BACKEND_CHOICE; use smtp via
# SES's SMTP interface, or ask the app owner to add an "ses" choice first)

# ---- ORCID OAuth (optional at launch) ----
# Leave unset until UA issues ORCID API credentials. Login without these still
# works via email/password; only the "Sign in with ORCID" button needs them.
# Provisioned as a SocialApp row via /admin/, not an env var — see Part I.
```

Notes:

- `DJANGO_SECRET_KEY` is read directly from the environment in
  `config/settings/prod.py` and **crashes Django at startup** if unset or
  empty — this is intentional (fail closed).
- `DJANGO_ALLOWED_HOSTS` must be exactly `i-connects.cossee.org` (comma-
  separate if you also serve `www.i-connects.cossee.org` — and add that as
  a second entry in the Caddyfile in Part G too).
- `SITE_URL` should match `DJANGO_ALLOWED_HOSTS` (with a scheme, e.g.
  `https://i-connects.cossee.org`). Unlike `DJANGO_SECRET_KEY`, an unset
  `SITE_URL` does **not** crash the app — it silently produces broken
  links in invitation/join-request/digest emails instead, so there's no
  startup signal that it's missing. Verify it by sending a real
  invitation after launch and checking the link in the email.
- `REDIS_PASSWORD` is mandatory, not merely recommended:
  `docker-compose.prod.yml` references it as `${REDIS_PASSWORD:?...}`, so
  `docker compose` refuses to start with a clear error if it is unset or
  empty. (Before this guard, an empty value brought Redis up with *no*
  password together with a health check that then failed authentication —
  Redis sat unhealthy forever and `web`, `celery` and `celery-beat`, all
  gated on it, never started.)
- Static files use whitenoise's `CompressedManifestStaticFilesStorage`
  through Django's `STORAGES` setting (Django 5.1 removed the old
  `STATICFILES_STORAGE` name, which is now silently ignored).
  `collectstatic` runs **when the image is built** (see the `Dockerfile`),
  not at container start — a deploy that changes static files needs
  `up -d --build`, and a plain restart never waits on it.
- `/healthz/` is an unauthenticated JSON endpoint that answers `200` only
  when the process is serving *and* can query Postgres, `503` otherwise.
  The `web` container's Docker health check and any external uptime
  monitor (Part L) both point at it.
- Never commit `.env`. It's covered by `.gitignore` after Part A8; double
  check with `git status` before any commit from this checkout.

Lock down the file permissions since it holds every production secret:

```bash
chmod 600 .env
```

### F1. Setting up Gmail SMTP

Emailing (signup verification, invitations, password reset, mailing-list
announcements, digests) uses a Google Mail account as the SMTP relay for
this deployment. This is a genuine, working option for Django's SMTP
backend, but Gmail imposes a few constraints worth knowing before you rely
on it — see the caveats at the end.

**1. Use (or create) a Google account dedicated to this app**, not a
personal one — e.g. something like `iconnects.notifications@gmail.com`.
Sharing a personal account's credentials with a server is a bad habit to
start, and a dedicated account makes credential rotation and offboarding
trivial later.

**2. Turn on 2-Step Verification.** Gmail's SMTP no longer accepts a
plain account password for third-party apps ("less secure app access" was
removed); an **App Password** is required instead, and Google only lets
you generate one once 2-Step Verification is enabled on the account:
`myaccount.google.com` → **Security** → **2-Step Verification** → follow
the setup flow (phone number or authenticator app).

**3. Generate an App Password:** `myaccount.google.com` → **Security** →
**2-Step Verification** → scroll to **App passwords** (or go directly to
`myaccount.google.com/apppasswords`). Create one named something like
`i-connects production`. Google shows it once as a 16-character string —
copy it immediately (spaces are just for display; strip them when pasting
into `.env`).

**4. Set the values in `.env`** (per the block above):
```ini
EMAIL_BACKEND_CHOICE=smtp
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=<the gmail address>
EMAIL_HOST_PASSWORD=<the 16-character App Password, no spaces>
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=i-connects <the gmail address>
```

**5. Test it end to end** once the stack is up:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web \
  python manage.py sendtestemail your-real-address@example.com
```
(Django's built-in `sendtestemail` command — sends a plain test message
using whatever `EMAIL_*` settings are active.) If that lands in your inbox,
also walk the real signup flow once — Part A5 makes email verification
**mandatory**, so a broken SMTP config silently locks out every new user,
not just this test.

**Caveats specific to using Gmail here, not a transactional provider:**

- **`DEFAULT_FROM_EMAIL` should be the same Gmail address you authenticate
  as** (or a verified "Send mail as" alias configured inside that Gmail
  account) — `noreply@i-connects.cossee.org` or any address Gmail doesn't
  recognize as belonging to the account will often get rewritten or
  flagged, since Gmail enforces alignment between the authenticated
  account and the `From` header.
- **The angle brackets around the address are required, not decorative.**
  It must be written exactly as `Display Name <address@example.com>`. If
  you drop the `<...>` (e.g. `i-connects your-account@gmail.com`), the
  whole string — display name, space, and all — gets parsed as the
  address, which then fails the alignment check above and every outgoing
  email bounces with `550 5.7.30 ... DKIM authentication didn't pass`.
  `manage.py check` warns about this specific mistake (`core.W001`).
- **Sending limits.** A regular Gmail account is capped at roughly 500
  recipients/day (Google Workspace accounts get a higher limit, commonly
  2000/day) — fine for auth emails and moderate invitation/notification
  volume, but a large mailing-list send (see the app's per-project mailing
  list feature) or a burst of signups could hit it. If that becomes a real
  constraint, `EMAIL_BACKEND_CHOICE=resend` (already wired up, just needs
  `RESEND_API_KEY`) is the lower-effort switch to a provider built for this.
- **If this Google account is a University of Alberta Workspace account**
  rather than a personal `gmail.com` one, an org admin may have App
  Passwords disabled entirely by policy (some Workspace orgs require OAuth2
  for SMTP instead). If App Passwords aren't available under **Security**
  for that account, that's why — coordinate with whoever administers UA's
  Workspace rather than assuming the steps above are missing something.
- **Treat the App Password exactly like every other secret in `.env`** —
  covered by A8's `.gitignore` fix and the `chmod 600` above already, but
  worth restating: it grants send-as access to that Gmail account.
- Revoking access later (offboarding, suspected leak) is a single click:
  `myaccount.google.com/apppasswords` → delete the entry — no password
  rotation on the Google account itself required, since App Passwords are
  independently revocable.

---

<a id="part-g"></a>
## Part G — Reverse proxy: `cossee-web-server` already runs a shared Caddy

`cossee-web-server` is a shared host: it already runs a **host-level Caddy**
(a systemd service, not a Docker container) that owns `:80`/`:443` for the
whole machine and fronts at least one other app. Confirmed live —
`docker-compose.prod.yml`'s own `caddy:` service fails to start with
`address already in use` because of this, and `docker ps` shows no Caddy
container at all, only a `caddy` process bound to `*:80`/`*:443` outside
Docker entirely. That means the committed `Caddyfile` and the `caddy:`
service in `docker-compose.prod.yml` (written for a single-app, dedicated
host) don't apply here — use the existing host Caddy instead.

### G1. Drop this repo's own `caddy` service; bind `web` to loopback only

**Already applied** in the committed `docker-compose.prod.yml` (see Part
A7's Bug 2/3 fixes, which land the same file changes): no `caddy:` service,
`web` publishes only `127.0.0.1:8000:8000`, and there's no `caddy_data`/
`caddy_config` volume. If your checkout predates this, diff against the
current file — this used to be a manual step and, on the host where the
data-loss incident happened, evidently wasn't applied.

Binding to `127.0.0.1` rather than `0.0.0.0` means only processes on the
host itself — the host Caddy — can reach gunicorn on `:8000`; the outside
world can't hit it directly, with or without a firewall rule.

This repo's `Caddyfile` is not used by any compose service; it is kept only
as the source text for the host Caddy site block installed in G3 (same
content, `127.0.0.1:8000` upstream).

### G2. DNS

Covered in Part D — the A record must resolve before G3's install can issue
a cert for it. Don't install the site file before DNS resolves: issuance
fails and retries in the background rather than succeeding on request.

### G3. Register your port, then add a site file

On `cossee-web-server` specifically, the host Caddy setup is more
structured than a single flat `Caddyfile`: it's config-managed by the
host's admin (`mzakir`), sites live as individual files under
`/etc/caddy/sites/` (imported by the main config), reloads go through a
`caddy-reload` wrapper rather than a raw `systemctl reload`, and ports are
tracked in a shared registry so two apps don't collide on the same
localhost port:

```bash
groups                              # confirm you're in the webdev group (added by the host admin)
cat /etc/caddy/PORTS.md             # claim your port entry (8000, for this app) before adding anything else
```

Get the site file from whoever administers the host Caddy — on this host,
that's a template staged by `mzakir` — **read it before installing it**:
it's about to become privileged, TLS-terminating config for this domain,
sourced from someone else's home directory, so verify it actually does
what it claims (proxies only to `127.0.0.1:8000`, no unexpected upstreams,
no wildcard host match) rather than trusting the description alone. The
confirmed-working version for this app:

```caddyfile
i-connects.cossee.org {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000 {
        # Django needs these to build absolute URLs and to see the real
        # client address rather than the proxy's.
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-For {remote_host}
        header_up Host {host}
    }
    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        -Server
    }
    log
}
```

One accepted trade-off worth knowing about: Caddy's `header` directive
*replaces* a response header of the same name rather than layering onto
it, so this template's `X-Frame-Options: SAMEORIGIN` overrides Django's own
`X_FRAME_OPTIONS = "DENY"` (`config/settings/prod.py`) — the browser sees
`SAMEORIGIN`, not `DENY`. Still a legitimate clickjacking mitigation, just
slightly looser than the app's own default. Not worth changing unilaterally
since it's a shared template — raise it with the host Caddy's admin if
strict `DENY` ever matters for this app specifically.

Install and reload — validate first, since a bad file here breaks every app
behind this Caddy, not just this one:

```bash
sudo caddy validate --config /etc/caddy/sites/i-connects.caddy 2>/dev/null || true  # if the wrapper doesn't validate first
sudo install -m 644 <path-to-the-reviewed-site-file> /etc/caddy/sites/i-connects.caddy
caddy-reload   # host-specific graceful-reload wrapper; no dropped connections
               # for this app or others behind the same Caddy
```

Caddy requests the certificate on the first real HTTPS request after this,
so the very first hit to `https://i-connects.cossee.org/` right after
`caddy-reload` may take an extra second or two.

### If this app is ever the *only* thing on a host

The original per-repo Dockerized `caddy:` service (the one G1 removes) is a
perfectly good, simpler default when nothing else shares the machine — one
`docker compose up`, TLS included, no host-level Caddy to coordinate with.
`cossee-web-server` isn't that kind of host, so this guide documents the
shared-host path as the one that actually applies here.

---

<a id="part-h"></a>
## Part H — Launch

**First, the data volumes.** Postgres data and uploaded media live in two
named volumes that the compose files declare `external: true`, so that
`docker compose down -v` can never delete them. Compose therefore does not
create them either — create them once per host:

```bash
docker volume create i-connects_postgres_data
docker volume create i-connects_media_data
```

On a host that already ran this stack, `docker volume ls` shows both names
already (the project name is fixed to `i-connects` in `docker-compose.yml`,
so they no longer depend on the checkout directory's name) — skip the
`create` lines. If it shows *differently named* volumes carrying real data
(for example from a checkout that wasn't called `i-connects`), copy the
data across before the first `up` rather than starting from an empty
database:

```bash
docker run --rm -v <old-volume>:/from:ro -v i-connects_postgres_data:/to alpine sh -c 'cp -a /from/. /to/'
```

**Then launch:**

```bash
cd ~/i-connects
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps                               # every service must reach (healthy)
docker compose logs --tail=100 web celery celery-beat
docker ps --format '{{.Names}}\t{{.Ports}}'    # confirm the state below
curl -sS -H 'Host: i-connects.cossee.org' http://127.0.0.1:8000/healthz/   # {"status": "ok", "database": "ok"}
```

What this does:

- `web` runs `migrate` and then `exec`s `gunicorn :8000` as `appuser`, bound
  to `127.0.0.1:8000` only — reachable from the host itself (i.e., the
  host-level Caddy from Part G), not from the network. `collectstatic`
  already ran when the image was built.
- TLS and the public `:80`/`:443` edge are handled entirely by the existing
  host-level Caddy (Part G) — this app doesn't run or manage a reverse
  proxy of its own on `cossee-web-server`.
- `db` and `redis` are unreachable from the host network or the public
  internet — verify with the `docker ps` line above; neither should show a
  `0.0.0.0:...` mapping. `redis` requires the password from Part F and
  keeps its queue on the `i-connects_redis_data` volume (append-only
  file), so queued emails survive a crash.
- Every service has a health check: `db` (`pg_isready` over TCP), `redis`
  (authenticated `PING`), `web` (`/healthz/` with the site's `Host`
  header), `celery` (`celery inspect ping` to its own node) and
  `celery-beat` (a heartbeat file the scheduler touches on every tick).
  `celery` and `celery-beat` are only started once `web` is healthy, i.e.
  once `migrate` has finished, so the first `docker compose ps` after a
  fresh deploy can take a minute to settle.

Create the platform's first admin account:

```bash
docker compose exec web python manage.py createsuperuser
```

Then, in `/admin/`, set that user's `role` to `ADMIN` (via the custom
`User` model) if you want access to the `/admin/console/` platform-admin
dashboard described in `STATUS.md`/`FOR_JIM.md`.

If Celery Beat's periodic jobs (daily digest, weekly digest, overdue
reminders, tmp-file cleanup) aren't already seeded:

```bash
docker compose exec web python manage.py setup_cron
```

---

<a id="part-i"></a>
## Part I — Post-launch verification

```bash
curl -I https://i-connects.cossee.org/                 # expect 200 or 302, not 5xx
curl -sS https://i-connects.cossee.org/healthz/        # {"status": "ok", "database": "ok"}
curl -sI https://i-connects.cossee.org/ | grep -i strict-transport-security
docker compose ps                                        # every service (healthy)
docker compose exec web python manage.py check --deploy  # Django's own deploy checklist
```

Manual checks:

- Visit `https://i-connects.cossee.org/` in a browser — landing page
  renders, padlock is valid (Let's Encrypt, not self-signed).
- Sign up with a real address you control; confirm the verification email
  arrives (Part A5 makes this mandatory) and the confirmation link works.
- `http://i-connects.cossee.org/` redirects to `https://`.
- Run the site through [Mozilla Observatory](https://developer.mozilla.org/en-US/observatory)
  or `testssl.sh` against the domain — HSTS, TLS version, and header grade
  should all come back clean given `prod.py`'s settings plus the Caddy
  headers from Part G.
- Confirm `/admin/` and `/admin/console/` are reachable only by
  authenticated staff/admin accounts (log out, hit both — expect a login
  redirect, not content).
- Walk the golden-path checklist in `FOR_JIM.md` end to end with the real
  domain.

If ORCID credentials have been issued by UA: create the `SocialApp` row
under `/admin/socialaccount/socialapp/` (provider: ORCID, client id/secret
from UA, site: `i-connects.cossee.org`) — no code or env change needed,
this is data-only.

---

<a id="part-j"></a>
## Part J — Day-2 operations

**Deploy a new commit:**

```bash
cd ~/i-connects
git fetch origin main
git checkout main && git pull origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs --tail=50 web
```

Migrations run automatically on container start (the prod `web` command
runs `migrate` and then `exec`s `gunicorn`); `collectstatic` ran during
`--build`. Watch the first `docker compose logs` after every deploy for
migration errors before walking away, and confirm `docker compose ps`
shows every service `(healthy)` — `celery` and `celery-beat` are not
recreated until `web` is.

Stopping or restarting is graceful: gunicorn receives SIGTERM directly
(35 s grace), Celery finishes in-flight tasks (60 s), and Postgres gets
SIGINT for a fast, clean shutdown (60 s). If `docker compose stop` takes
longer than that, something is wedged — read the logs rather than raising
the timeouts.

**Postgres major versions:** the `db` service is pinned to `postgres:16`.
Never bump the major version in place — the data directory format is
version-specific and the container will refuse to start on the old data.
Upgrading means `scripts/backup.sh`, a new empty volume, then
`scripts/restore.sh` into the new major (or `pg_upgrade`), planned as its
own change.

**Roll back a bad deploy:**

```bash
cd ~/i-connects
git checkout <previous-good-sha>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

If the bad deploy included a migration that isn't safely reversible, don't
`git checkout` past it blindly — write and apply a compensating migration
instead of relying on Django's `migrate <app> <previous>` against data that
newer code may already have written in the new shape.

**Tail logs:**

```bash
docker compose logs -f web
docker compose logs -f celery celery-beat
sudo journalctl -u caddy -f   # host-level Caddy (Part G) — not a compose service on this host
```

**Review dependency updates:** `.github/workflows/ci.yml` runs `pytest` on
every push/PR, and `.github/dependabot.yml` opens weekly PRs for `pip`,
`npm`, and GitHub Actions. Don't let Django/psycopg security-release PRs
sit — those are exactly what Part A4 flagged as already stale once.

---

<a id="part-k"></a>
## Part K — Backups & disaster recovery

**What to back up.** Everything durable is in two Docker volumes:
`i-connects_postgres_data` (the database — users, projects, tasks,
sessions, the beat schedule) and `i-connects_media_data` (`MEDIA_ROOT`;
today only scratch files under `tmp/`, but any future upload lands here).
Redis holds only in-flight queue state and is not backed up. Static files
are rebuilt from the image.

**One-off backup**, from the checkout directory:

```bash
mkdir -p ~/backups
scripts/backup.sh
```

This writes `~/backups/db-<timestamp>.dump` (a `pg_dump -Fc` custom-format
archive, verified with `pg_restore --list` before it is kept) and
`~/backups/media-<timestamp>.tar.gz`, then deletes copies older than 30
days. `BACKUP_DIR` and `RETENTION_DAYS` override the defaults. The dump
runs inside the `db` container with that container's own credentials, so
nothing is read from `.env`.

**Nightly cron**, `crontab -e` as `deploy`:

```cron
0 3 * * * cd ~/i-connects && scripts/backup.sh >> ~/backups/backup.log 2>&1
```

(Nightly for 30 days locally; ship copies off-host — see below — for
anything you actually need to survive a lost disk.)

**Off-host copy** — cron dumps alone don't protect against losing the VM.
Sync `~/backups` to durable off-host storage on the same schedule (object
storage, another host via `rsync`/`rclone`, whatever your org already
uses). This guide intentionally doesn't hardcode a specific provider since
none was specified — pick one and wire it into the cron job above before
calling backups "done."

**Restore.** `scripts/restore.sh` restores a database dump and/or a media
tarball. Without `--target` it replaces the **live** database: it stops
`web`, `celery` and `celery-beat`, runs `pg_restore --clean` into the
production database, extracts the media tarball over `/app/media`, and
brings the app back up:

```bash
scripts/restore.sh --db ~/backups/db-<timestamp>.dump --media ~/backups/media-<timestamp>.tar.gz --yes
```

**Quarterly restore drill** — a backup nobody has restored is a hope, not
a backup. `--target` restores into a fresh, separately named database on
the same Postgres server and leaves production untouched; spot-check a
table, then drop it:

```bash
scripts/restore.sh --db ~/backups/db-<timestamp>.dump --target drill_$(date +%Y%m) --yes
docker compose exec -T db psql -U iconnects -d drill_$(date +%Y%m) -c 'select count(*) from accounts_user;'
docker compose exec -T db dropdb -U iconnects drill_$(date +%Y%m)
```

Older plain-SQL dumps (`db-YYYY-MM-DD.sql.gz`, from before the script
existed) restore with
`gunzip -c <file> | docker compose exec -T db psql -U iconnects -d iconnects`.

**Disk snapshots** (whatever hypervisor/host provider `cossee-web-server`
runs on) are the fastest full-system recovery path if available — schedule
daily snapshots with a 7+ day retention on the boot/data disk as a
complement to, not a replacement for, the `pg_dump` backups above (a
snapshot restores the whole VM state; a `pg_dump` restores just the data
into a fresh deploy).

---

<a id="part-l"></a>
## Part L — Monitoring & log rotation

- **Container logs** grow unbounded under Docker's default `json-file`
  driver. `docker-compose.prod.yml` caps every service at 5 × 10 MB via
  the shared `x-logging` anchor; leave it in place (or set the same
  `log-opts` globally in `/etc/docker/daemon.json`).
- **Unhealthy containers are not restarted by Docker.** A failing health
  check only shows in `docker compose ps` and gates `depends_on`;
  `restart: unless-stopped` acts on process exit, not on health. The
  repo's `scripts/restart-unhealthy.sh` restarts every container of this
  project whose check is failing. Run it from cron every two minutes, as
  `deploy` (`mkdir -p ~/logs` first):
  ```cron
  */2 * * * * cd ~/i-connects && scripts/restart-unhealthy.sh >> ~/logs/restart-unhealthy.log 2>&1
  ```
  Each restart is one line in that log. A container that gets restarted
  repeatedly is a bug to chase in `docker compose logs`, not something to
  leave to the cron job.
- **Uptime**: point an external uptime check (whatever your org already
  uses — even a simple cron `curl` + alert is fine to start) at
  `https://i-connects.cossee.org/healthz/` so a database outage is caught
  too, not just a dead proxy. It answers `200` with `{"status": "ok", ...}`
  or `503`.
- **Disk space**: Postgres and container logs both grow over time —
  `df -h /` periodically, or alert on it. `docker system df` shows what
  Docker itself is holding; `docker image prune` after deploys reclaims
  old, unused build layers.
- **Cert renewal**: the host-level Caddy (Part G) renews Let's Encrypt certs
  automatically; confirm once a quarter with
  `sudo journalctl -u caddy | grep -i certificate` that a renewal actually
  happened in the preceding ~60 days rather than silently failing. This
  affects every app behind that Caddy, not just this one.

---

<a id="part-m"></a>
## Part M — Secret rotation / incident response

If any secret in `.env` is suspected compromised (leaked log, compromised
laptop, departing team member with access):

1. Generate a new value (same `secrets.token_hex(32)` approach as Part F).
2. `DJANGO_SECRET_KEY` rotation invalidates all existing sessions and
   signed tokens (password reset links, invitation links) — expected, not
   a bug.
3. `POSTGRES_PASSWORD` rotation: also `ALTER USER iconnects WITH PASSWORD
   '<new>'` inside the `db` container, then update `.env`'s
   `DATABASE_URL`, before restarting `web`/`celery`/`celery-beat`.
4. `REDIS_PASSWORD` rotation: update `docker-compose.prod.yml`'s Redis
   `command` is unnecessary (it reads `${REDIS_PASSWORD}` from `.env`
   already); just update `.env` and restart the stack.
5. Any SMTP/Resend/SES credential: rotate at the provider, update `.env`.
6. After any rotation:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```
   (no `--build` needed for secret-only changes — compose recreates
   containers with the new env values.)
7. Check `/my/activity/` and `/projects/<id>/activity/` (the audit log) and
   Django admin's read-only audit view for anything suspicious in the
   affected window.

---

<a id="part-n"></a>
## Part N — Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `docker compose up`: `address already in use` on :80/:443 | This host already runs Caddy (host-level or another app's) | You're on `cossee-web-server`, a shared host — see Part G. Drop this repo's own `caddy:` service; use the existing host Caddy instead. |
| Caddy logs `no such host` / `dns problem` | A-record not propagated | `dig +short i-connects.cossee.org`; wait or fix DNS (Part D). |
| Browser: `ERR_CONNECTION_REFUSED` on :443 | UFW/firewall blocks 443 | `sudo ufw status`; confirm `443/tcp` is allowed (Part B4). |
| Django: `DisallowedHost` | `DJANGO_ALLOWED_HOSTS` mismatch | Must contain `i-connects.cossee.org` exactly (Part F). |
| Infinite redirect loop | `SECURE_SSL_REDIRECT=True` but `X-Forwarded-Proto` not forwarded | Confirm the `header_up X-Forwarded-Proto {scheme}` line is still present in `/etc/caddy/sites/i-connects.caddy` (Part G). |
| `relation "..." does not exist` | Migrations didn't run | `docker compose exec web python manage.py migrate`. |
| Static files 404, or a page 500s with `Missing staticfiles manifest entry` | The image was built without `collectstatic` (or a template references a static file that doesn't exist) | `collectstatic` runs at image build: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`. For the manifest error, the named file is missing from `static/` — fix the template or add the file, then rebuild. |
| `docker compose up` ends with `dependency failed to start: container i-connects-web-1 is unhealthy` | `web`'s health check (`/healthz/` with the site `Host` header) is failing, so `celery`/`celery-beat` were not started | `docker inspect --format '{{json .State.Health}}' i-connects-web-1` shows the last probe outputs. `migrate` still running or failed → `docker compose logs web`. A `400` means `DJANGO_ALLOWED_HOSTS` is empty or malformed (the check uses its first entry). A `503` means gunicorn is up but cannot reach Postgres. Fix, then `up -d` again. |
| `docker compose up` fails immediately with `required variable REDIS_PASSWORD is missing a value` | `REDIS_PASSWORD` unset or empty in `.env` | Set it (Part F) and make `REDIS_URL` / `CELERY_*` carry the same password. This is a deliberate guard — see Part F's note. |
| `docker compose ps` shows a service `(unhealthy)` but it keeps running | Docker never restarts on health status alone | Expected; the Part L cron (`scripts/restart-unhealthy.sh`) restarts it within two minutes. Read the health log (`docker inspect` as above) for the cause. |
| `external volume "i-connects_postgres_data" not found` (or `..._media_data`) | The data volumes are `external: true` and were never created on this host | `docker volume create` both names — Part H, first block. Never work around this by removing `external: true`: it is what stops `down -v` from deleting the database. |
| Cert stuck on staging / rate-limited | Repeated failed issuance attempts | Fix the underlying DNS issue first, then `caddy-reload` again. Let's Encrypt rate limits are per-domain-per-week — don't loop this; if truly stuck, whoever manages the host Caddy needs to clear its stored account/cert state, which affects every app behind it. |
| `docker ps` shows `0.0.0.0:5432->...` or `0.0.0.0:6379->...` | Part A7's `!reset []` fix not applied, or applied but not rebuilt | Re-check the `docker-compose.prod.yml` diff, then `up -d` (recreation, not just editing the file, is what takes effect). This also means UFW was never protecting these — see Part B4's note. |
| A signup with an unconfirmed/fake email still gets a logged-in session | Expected today, not a config bug — see Part A5's note: the app's `SignupView`/`LoginView` are hand-rolled and never check email-verification status, so `ACCOUNT_EMAIL_VERIFICATION=mandatory` doesn't gate this path at all | Real fix is an app-code change to `apps/accounts/views.py`, tracked separately from deployment config — don't spend time debugging `.env`/SMTP for this specific symptom. |
| No confirmation email ever arrives for a regular (non-ORCID) signup | Expected today — the primary signup path doesn't call allauth's email-confirmation flow at all, so nothing is sent to debug | Only the "welcome email" (`send_welcome_email`) fires on regular signup; allauth's confirmation email only exists for the ORCID social-login path. `docker compose logs web \| grep -i mail` will show the welcome email attempt, not a confirmation one. |
| Gmail SMTP: `535 5.7.8 Username and Password not accepted` | Using the account's normal password instead of an App Password, or 2-Step Verification isn't on | Re-check Part F1 — `EMAIL_HOST_PASSWORD` must be the 16-character App Password from `myaccount.google.com/apppasswords`, which doesn't exist until 2-Step Verification is enabled. |
| Gmail account has no "App passwords" option under Security | Workspace org policy disables them | If this is a University of Alberta Workspace account rather than a personal one, an admin may need to allow App Passwords, or the account may require OAuth2 for SMTP instead — this isn't a config mistake on your end. |
| Gmail-sent mail lands in spam, or `From` gets silently rewritten | `DEFAULT_FROM_EMAIL` doesn't match the authenticated Gmail address/alias | Set `DEFAULT_FROM_EMAIL` to the same address as `EMAIL_HOST_USER` (or a verified Gmail "Send mail as" alias), per Part F1. |
| Recipient's mail server bounces `550 5.7.30 ... DKIM authentication didn't pass` (affects *every* outgoing email — signup confirmation, password reset, invitations — not just one type) | Almost always `DEFAULT_FROM_EMAIL` is missing its angle brackets. It must be `Display Name <address@example.com>`; written as `Display Name address@example.com` (no `<...>`), the whole string *including the space* is parsed as the address, which then doesn't match the SMTP-authenticated account (`EMAIL_HOST_USER`) — Gmail can't align the two and rejects the message. `manage.py check` (which `manage.py migrate` also runs) catches this at deploy time as `core.W001`. Only if the format is already correct and this persists: the sending account may be a Google Workspace domain that needs DKIM signing turned on by an admin. | Fix the format first: `DEFAULT_FROM_EMAIL=i-connects <you@gmail.com>`, matching `EMAIL_HOST_USER` exactly, then restart `web`/`celery` and re-test. Only if that's already correct: for a Workspace domain, enable it under Admin console → Apps → Google Workspace → Gmail → Authenticate email, then publish the generated TXT record at the domain's DNS. |
| Invitation/join-request/digest email link is broken — e.g. `http:///invites/<code>/` (empty host) | `SITE_URL` isn't set. Those emails are sent from Celery tasks with no HTTP request to build an absolute URL from, so they fall back to `SITE_URL`; left unset it silently degrades to a bare relative path, which the recipient's mail client then mangles into a schemeless-host link | Set `SITE_URL=https://i-connects.cossee.org` in `.env` (Part F) and restart the `web` and `celery` services, then send a fresh invitation to confirm the link is now absolute. |
| `manage.py check --deploy` warns about `SECRET_KEY` or `ALLOWED_HOSTS` | `.env` not actually loaded, or `DJANGO_SETTINGS_MODULE` wrong | `docker compose exec web env \| grep DJANGO`; confirm it reads `config.settings.prod`. |
| Container runs as root (`docker compose exec web id` shows `uid=0`) | Part A3 not applied/rebuilt | Re-check the `Dockerfile` diff, then `up -d --build` (not just `up -d`). |

---

<a id="part-o"></a>
## Part O — Tear-down

Stop the stack but keep data:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

Wipe everything — **irreversible, confirm you have a current off-host
backup (Part K) first**. `down -v` removes the containers, the network and
the Redis volume, but deliberately *not* the database or media volumes:
those are `external: true` precisely so this command cannot take them.
Removing them is a separate, explicit step:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
docker volume rm i-connects_postgres_data i-connects_media_data
```

Since Part G put this app behind the host's shared Caddy rather than a
Caddy this stack owns, also remove `/etc/caddy/sites/i-connects.caddy` and
run `caddy-reload`, and free the port entry in `/etc/caddy/PORTS.md` —
otherwise the shared proxy keeps trying to route to a `127.0.0.1:8000`
that's no longer listening, its error logs (and other apps behind it) get
noisier for no reason, and the port stays claimed for an app that no
longer exists.

To decommission the host entirely: take a final `pg_dump` + media backup,
confirm both landed off-host, then deprovision the server through whatever
infrastructure/hosting console manages `cossee-web-server`, and release the
DNS `A` record for `i-connects.cossee.org` once nothing points at it.
