# Production Deployment — i-connects

End-to-end instructions for running i-connects in production on
`cossee-web-server` (Ubuntu 24.04 LTS, x86_64), over SSH. Domain:
**`i-connects.cossee.org`**.

Stack: Docker Compose running Django/gunicorn, Postgres 16, Redis 7, a Celery
worker and Celery Beat, fronted by the host's shared Caddy 2 for automatic
Let's Encrypt TLS. This guide describes the committed `Dockerfile`,
`docker-compose.yml` and `docker-compose.prod.yml` as they are; where a
setting or behaviour is explained here, the same explanation sits as a
comment next to the line in those files. `DEMO_DEPLOYMENT.md` is retired and
points here.

Part A summarises what the repository already guarantees (the security
audit in `SECURITY_AUDIT.md` and the container hardening review) and how to
verify each point, rather than listing fixes to apply — everything a
deployment depends on has landed on `main`. Two audit findings that remain
open are app-code items and are named there so nobody mistakes them for
configuration.

---

## Contents

- [Architecture at a glance](#architecture)
- [Part A — What the repository already guarantees](#part-a)
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
- [Part L — Monitoring, health, and log rotation](#part-l)
- [Part M — Secret rotation / incident response](#part-m)
- [Part N — Troubleshooting](#part-n)
- [Part O — Tear-down](#part-o)

---

<a id="architecture"></a>
## Architecture at a glance

One image (built from the `Dockerfile`) runs as three services; Postgres and
Redis are stock images. `docker-compose.yml` holds everything that is true in
both development and production — health checks, startup ordering, signal
handling, shutdown timeouts, the fixed project and volume names.
`docker-compose.prod.yml` layers on only what differs in production.

| Service | Image | Reachable from | Persistent data | Health check | Stop |
| --- | --- | --- | --- | --- | --- |
| `web` | this repo | `127.0.0.1:8000` on the host only (the host Caddy proxies to it) | `i-connects_media_data` → `/app/media` | `GET /healthz/` with the site `Host` header: 200 only when gunicorn serves **and** Postgres answers | SIGTERM to gunicorn directly (`exec`), 35 s grace |
| `celery` | this repo | not published | shares `i-connects_media_data` | `celery inspect ping` to its own node — catches a wedged worker, not just a dead one | warm shutdown, 60 s grace |
| `celery-beat` | this repo | not published | none (schedule lives in Postgres) | heartbeat file the scheduler touches on every tick, must be < 1 min old | 15 s grace |
| `db` | `postgres:16` | not published | `i-connects_postgres_data` → `/var/lib/postgresql/data` | `pg_isready` over TCP (the init-time temporary server can't fake it) | SIGINT = fast clean shutdown, 60 s grace |
| `redis` | `redis:7` | not published | `i-connects_redis_data` → `/data`, append-only file, `noeviction` | authenticated `PING` | 30 s grace |

Things that follow from this table and come up repeatedly below:

- **Startup order is gated on health, not on "started".** `web` waits for
  `db` and `redis` to be healthy; `celery` and `celery-beat` wait for `web`
  to be healthy, which happens only after `migrate` has finished. A fresh
  deploy therefore takes about a minute to settle in `docker compose ps`.
- **`collectstatic` runs when the image is built**, not when a container
  starts. A `web` container start (`scripts/start-web.sh`) waits for
  Postgres to answer, runs `migrate`, then `exec`s the server.
- **The project name is fixed to `i-connects`** in `docker-compose.yml`, so
  container, network and volume names never depend on the checkout
  directory's name.
- **The two data volumes are `external: true`.** Compose neither creates
  nor deletes them; `docker volume create` once per host (Part H), and
  `docker compose down -v` cannot remove them (Part O).
- **A health check going red does not restart anything by itself.** Docker
  only reports it and uses it for ordering. Part L installs the cron job
  that restarts unhealthy containers.
- **`REDIS_PASSWORD` is mandatory** in production: the compose file refuses
  to start without it instead of bringing Redis up unauthenticated.

---

<a id="part-a"></a>
## Part A — What the repository already guarantees

`SECURITY_AUDIT.md` (2026-07-08) graded the tree against a live deployment.
Every Critical and High finding, and the Medium findings that are
deployment configuration, are closed on `main`; each item below names the
finding, states what the tree does now, and gives a one-line check you can
run yourself. A9 lists the two findings that are still open and why they
are not part of this guide's scope.

### A1. `.dockerignore` keeps secrets and junk out of the image (closes C1)

`Dockerfile` does `COPY . .`. `.dockerignore` excludes `.env` and every
`.env.*` variant except `.env.example`, `.git`, `.github`, `node_modules`,
`media/`, `staticfiles/`, local virtualenvs, a stray `db.sqlite3`, logs,
Celery Beat state files, editor directories, and all Markdown except
`README.md`.

```bash
docker build -t iconnects:check .
docker run --rm iconnects:check sh -c 'ls -a /app | grep -cE "^\.env$|^\.git$|^node_modules$"'   # expect 0
```

### A2. Settings fail closed (closes C2)

The image defaults `DJANGO_SETTINGS_MODULE=config.settings.prod`, and
`config/settings/prod.py` reads `DJANGO_SECRET_KEY` with a hard
`os.environ[...]`, so a container with a missing or empty secret crashes at
import instead of quietly serving development settings (`DEBUG=True`,
`ALLOWED_HOSTS=["*"]`, a public `SECRET_KEY`). `DEBUG` is hard-coded
`False` in prod, `ALLOWED_HOSTS` comes only from `DJANGO_ALLOWED_HOSTS`.

```bash
docker run --rm iconnects:check python manage.py check            # expect a KeyError on DJANGO_SECRET_KEY
docker run --rm -e DJANGO_SECRET_KEY=x -e DJANGO_ALLOWED_HOSTS=x iconnects:check python manage.py check   # expect "System check identified no issues" or warnings only
```

### A3. Non-root, read-only application code (closes H2)

The final stage creates `appuser` (uid 1000) and switches to it before the
server starts. Application code is root-owned and read-only to the
process; only `/app/staticfiles` (written at build time) and `/app/media`
(the media volume) belong to `appuser`. `PYTHONDONTWRITEBYTECODE=1` means
nothing needs to write elsewhere.

```bash
docker run --rm iconnects:check id -u                     # 1000
docker run --rm iconnects:check sh -c 'touch /app/x'      # Permission denied
```

### A4. Current, pinned dependencies (closes H3, H4, M7)

`requirements.txt` pins `Django==5.2.*` and `psycopg[binary]==3.2.*` to the
newest patch at install time; the CSS build stage and CI both use Node 22;
`.github/dependabot.yml` opens weekly update PRs for pip, npm and GitHub
Actions. The runtime image installs no compiler or libpq headers — every
requirement is a wheel or pure Python — so adding a dependency that needs
compilation fails the CI image build loudly rather than silently bloating
the image.

Don't let Django or psycopg security-release PRs sit; those are exactly
what H3/H4 flagged as stale once before.

### A5. Email verification is enforced in the app, not just in allauth (closes H1)

The finding was that `AcceptOrDeclineInviteView` trusts
`request.user.email`, so anyone who could sign up with an address they
didn't own could take an invitation meant for its real owner. Closed in
`apps/accounts`:

- `SignupView` creates the account, sends a confirmation link, and lands on
  a "check your inbox" page — it does **not** log the new user in.
- `LoginForm` refuses accounts whose `email_confirmed_at` is unset, before
  `authenticate()` even runs; a "resend confirmation" flow exists and is
  per-IP throttled.
- Only a confirmed session can therefore reach `AcceptOrDeclineInviteView`,
  whose email match now means something.
- `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` plus `ACCOUNT_EMAIL_REQUIRED`
  cover the allauth path (ORCID social login).

Consequence for deployment: **outbound email is load-bearing.** A broken
SMTP configuration doesn't just lose notifications, it locks every new user
out at signup. Part F sets it up and Part I tests it end to end.

### A6. `seed_demo` refuses to run in production (closes M3)

`apps/core/management/commands/seed_demo.py` raises `CommandError` when
`DEBUG` is `False`. Since A2 fixes production to `DEBUG=False`, the command
is simply unavailable there. Never try to work around it against
`i-connects.cossee.org`.

### A7. Compose override semantics and network exposure (closes M4)

Two facts about `docker compose -f a.yml -f b.yml` that this stack depends
on, and that have bitten this deployment before:

1. **List fields merge by union, not replacement.** `ports:`, `volumes:`,
   `expose:` and similar lists in an override file are *appended* to the
   base file's entries. A bare `ports: []` in the override therefore clears
   nothing — the base's `"5432:5432"` stays published, and the base's
   `.:/app` bind mount stays in place. The override file uses Compose's
   explicit tags instead: `!reset []` empties a list; `!override` replaces
   it with new content (`!reset` followed by content silently drops the
   content). Needs Compose v2.24 or newer; `docker compose version`.
2. **Map fields merge per key.** `healthcheck:`, `depends_on:`, `logging:`,
   `environment:` in the override change only the keys they mention, which
   is how the prod file swaps `web`'s health-check command while keeping
   the base file's interval and retries.

What production publishes as a result:

- `web` → `127.0.0.1:8000` only. Nothing but processes on the host itself
  (the host Caddy) can reach gunicorn.
- `db`, `redis` → not published at all.
- `redis` requires the password from `.env`; the compose file uses
  `${REDIS_PASSWORD:?...}` so an unset or empty value is a start-up error,
  not an unauthenticated broker.

Verify after any change to either compose file — the rendered config is
the truth, not the source:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config | grep -nE "published|host_ip|type: bind"
docker ps --format '{{.Names}}\t{{.Ports}}'    # only web, only 127.0.0.1:8000->8000
```

Related: **UFW is not a backstop for Docker port publishing.** Docker
inserts its own `iptables`/`DOCKER-USER` rules ahead of UFW's, so a
container that ends up published on `0.0.0.0` is reachable from the
internet regardless of the host firewall (Part B4). Keeping `db` and
`redis` unpublished is what protects them.

### A8. `.gitignore` covers every env-file spelling (closes M5)

`.env*` is ignored with `!.env.example` as the only exception, so
`.env.prod`, `.env.production`, `.env.local` and friends can't be committed
by accident. `git status` before any commit from the server checkout
should never list an env file.

### A9. Still open — app-code findings, not deployment configuration

Named here so they are not mistaken for something `.env` or compose can
fix. Both belong to whoever owns the application code.

- **M1 — CSV formula injection in contribution exports.**
  `apps/contributions/views.py` writes user-controlled cells (project and
  task names, user names) with `csv.writer` and no guard against a leading
  `=`, `+`, `-` or `@`. Recommended before exports are handed to PIs with
  untrusted project membership.
- **M2 — no per-IP throttle on the login endpoint.** Signup, password
  reset and resend-confirmation are throttled per IP
  (`apps/accounts/throttle.py`); login has only the per-email lockout
  (`apps/accounts/lockout.py`), so a slow, distributed guess rate against a
  known address never trips anything. The audit's suggested fix is to
  extend the existing throttle to `LoginView`.

### A10. Verify the posture

From a checkout of `main`, before pulling onto the server:

```bash
pytest -q                                                    # the suite passes on Python 3.12
docker build -t iconnects:check .                            # includes collectstatic; no compiler needed
docker run --rm iconnects:check id -u                        # 1000
docker run --rm iconnects:check test -f /app/staticfiles/staticfiles.json && echo "static manifest present"
test -f .env || cp .env.example .env                         # the services' env_file must exist for `config` to render
REDIS_PASSWORD=check-only docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet && echo "prod stack renders"
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet 2>/dev/null || echo "and refuses without REDIS_PASSWORD (expected)"
```

(A variable set in the shell wins over `.env` for Compose interpolation,
which is why the second line renders and the third — with the example
file's empty `REDIS_PASSWORD=` — must not.)

CI (`.github/workflows/ci.yml`) runs the same checks on every push and PR:
tests, the CSS build, both compose renders (including that the prod stack
*refuses* an empty `REDIS_PASSWORD`), the shell scripts' syntax, and the
image build with the appuser and static-manifest assertions above.

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

- **Fail2ban (B5) is load-bearing, not optional.** It's what turns repeated
  bad-password attempts from a given IP into an automatic ban — confirm it's
  actually running (`sudo fail2ban-client status sshd`) before you consider
  this step done.
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
  `PasswordAuthentication no` later removes the remaining attack surface
  entirely without removing anyone's access, since their keys already work.

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

No other port should be open. Postgres (`5432`) and Redis (`6379`) never
need a UFW rule — `docker-compose.prod.yml` keeps them off the host network
entirely (Part A7). **Don't treat UFW as a backstop for that**: Docker
publishes container ports by inserting its own `iptables`/`DOCKER-USER`
rules ahead of UFW's, so a container published to `0.0.0.0` is reachable
from the internet regardless of what UFW says. UFW here is for the host's
own listening processes (SSH) — it is not a safety net for Docker port
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
docker compose version     # v2.24 or newer — the compose files use !reset/!override (Part A7)
```

Docker's daemon starts on boot and restarts containers marked
`restart: unless-stopped` with it, so a host reboot brings the stack back
without anyone logging in. At daemon start Docker restarts containers by
policy and ignores compose's `depends_on`, so containers can come up before
Postgres: `web` handles that itself (`scripts/start-web.sh` retries the
database connection for up to a minute before it does anything else), while
`celery` and `celery-beat` may restart a few times until it answers. Both
are expected and self-heal; see Part N if they don't.

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
git log -1 --oneline
```

Clone into `~/i-connects`. The compose project name is fixed in
`docker-compose.yml`, so a different directory name would still work — but
`~/i-connects` is what every cron line and command in this guide assumes.

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

# ---- Redis / Celery ----
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

# smtp, via a Gmail account — see F1 below for how to get
# EMAIL_HOST_PASSWORD (it is NOT the account's normal login password):
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=<your-account@gmail.com>
EMAIL_HOST_PASSWORD=<16-character App Password, no spaces>
EMAIL_USE_TLS=True

# resend (use instead of the smtp block above; set EMAIL_BACKEND_CHOICE=resend):
# RESEND_API_KEY=<key>

# ses: django-ses is installed but "ses" is not wired into EMAIL_BACKEND_CHOICE
# today — use smtp via SES's SMTP interface, or ask the app owner to add an
# "ses" choice first.

# ---- ORCID OAuth (optional at launch) ----
# Leave unset until UA issues ORCID API credentials. Login without these still
# works via email/password; only the "Sign in with ORCID" button needs them.
# Provisioned as a SocialApp row via /admin/, not an env var — see Part I.
```

Notes:

- `DJANGO_SECRET_KEY` is read directly from the environment in
  `config/settings/prod.py` and **crashes Django at startup** if unset or
  empty — this is intentional (fail closed, Part A2).
- `DJANGO_ALLOWED_HOSTS` must be exactly `i-connects.cossee.org` (comma-
  separate if you also serve `www.i-connects.cossee.org` — and add that as
  a second entry in the Caddy site block in Part G too). The `web`
  container's health check sends the **first** entry as its `Host` header,
  so keep the real domain first.
- `SITE_URL` should match `DJANGO_ALLOWED_HOSTS` (with a scheme, e.g.
  `https://i-connects.cossee.org`). Unlike `DJANGO_SECRET_KEY`, an unset
  `SITE_URL` does **not** crash the app — it silently produces broken
  links in invitation/join-request/digest emails instead (`manage.py
  check` warns as `core.W002`). Verify it by sending a real invitation
  after launch and checking the link in the email.
- `REDIS_PASSWORD` is mandatory, not merely recommended:
  `docker-compose.prod.yml` references it as `${REDIS_PASSWORD:?...}`, so
  `docker compose` refuses to start with a clear error if it is unset or
  empty. (Before this guard, an empty value brought Redis up with *no*
  password together with a health check that then failed authentication —
  Redis sat unhealthy forever and `web`, `celery` and `celery-beat`, all
  gated on it, never started.) The same password has to appear in the
  three `redis://` URLs.
- `DEFAULT_FROM_EMAIL` must be written as `Display Name <address>` — the
  angle brackets are required, see F1. `manage.py check` warns as
  `core.W001` if they're missing.
- Static files use whitenoise's `CompressedManifestStaticFilesStorage`
  through Django's `STORAGES` setting (Django 5.1 removed the old
  `STATICFILES_STORAGE` name, which is now silently ignored).
  `collectstatic` runs **when the image is built** (see the `Dockerfile`),
  not at container start — a deploy that changes static files needs
  `up -d --build`, and a plain restart never waits on it.
- `/healthz/` is an unauthenticated JSON endpoint that answers `200` only
  when the process is serving *and* can query Postgres, `503` otherwise.
  The `web` container's Docker health check and any external uptime
  monitor (Part L) both point at it. It is exempt from the HTTPS redirect
  so the in-container probe gets a real answer, not a 301.
- Never commit `.env`. `.gitignore` covers every `.env*` spelling (Part
  A8); double check with `git status` before any commit from this checkout.

Lock down the file permissions since it holds every production secret:

```bash
chmod 600 .env
```

### F1. Setting up Gmail SMTP

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
also walk the real signup flow once — email confirmation is **mandatory**
(Part A5), so a broken SMTP config silently locks out every new user, not
just this test.

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
  covered by `.gitignore` and the `chmod 600` above already, but worth
  restating: it grants send-as access to that Gmail account.
- Revoking access later (offboarding, suspected leak) is a single click:
  `myaccount.google.com/apppasswords` → delete the entry — no password
  rotation on the Google account itself required, since App Passwords are
  independently revocable.

---

<a id="part-g"></a>
## Part G — Reverse proxy: `cossee-web-server` already runs a shared Caddy

`cossee-web-server` is a shared host. It runs a **host-level Caddy** (a
systemd service, not a Docker container) that owns `:80`/`:443` for the
whole machine and fronts other apps besides this one. This stack therefore
has no `caddy` service of its own: `web` listens on the host's loopback
only, and the host Caddy terminates TLS and proxies to it. The committed
`Caddyfile` is the source text for the site block installed in G3 — no
compose service mounts or reads it.

(If this app is ever the *only* thing on a dedicated host, a per-repo caddy
container is the simpler option; the one-file override for that is in
`DEMO_DEPLOYMENT.md`. It does not apply on `cossee-web-server`.)

### G1. `web` is bound to loopback only

`docker-compose.prod.yml` publishes `web` as `127.0.0.1:8000:8000`, so only
processes on the host itself — the host Caddy — can reach gunicorn on
`:8000`; the outside world can't hit it directly, with or without a
firewall rule. `db` and `redis` are not published at all (Part A7).
Confirm after launch with `docker ps --format '{{.Names}}\t{{.Ports}}'`.

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
sudo ss -ltnp 'sport = :8000'       # and confirm nothing on the host is already listening on it
```

If 8000 is already taken by another app, don't fight over it: pick a free
port, set `WEB_PORT=<port>` in `.env` (the compose file publishes `web` on
`127.0.0.1:${WEB_PORT:-8000}`), use that port in the `reverse_proxy` line
below, and register it in `PORTS.md`. The container itself always listens
on 8000; only the host side moves.

Get the site file from whoever administers the host Caddy — on this host,
that's a template staged by `mzakir` — **read it before installing it**:
it's about to become privileged, TLS-terminating config for this domain,
sourced from someone else's home directory, so verify it actually does
what it claims (proxies only to `127.0.0.1:8000`, no unexpected upstreams,
no wildcard host match) rather than trusting the description alone. The
confirmed-working version for this app is the repo's `Caddyfile`:

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

`X-Forwarded-Proto` is what lets Django (`SECURE_PROXY_SSL_HEADER`) treat
proxied requests as HTTPS; without it `SECURE_SSL_REDIRECT=True` loops
forever. The `web` health check does not go through Caddy and is exempt
from that redirect, so it keeps working whatever the proxy is doing.

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
docker ps --format '{{.Names}}\t{{.Ports}}'    # only web, only 127.0.0.1:8000->8000 (or your WEB_PORT)
curl -sS -H 'Host: i-connects.cossee.org' http://127.0.0.1:8000/healthz/   # {"status": "ok", "database": "ok"}
```

What this does:

- The image is built: CSS compiled from the lockfile, Python requirements
  installed, `collectstatic` run into the image as `appuser`.
- `db` and `redis` start and turn healthy; `redis` requires the password
  from Part F and keeps its queue on the `i-connects_redis_data` volume
  (append-only file), so queued emails survive a crash.
- `web` waits for both, then runs `scripts/start-web.sh`: it retries the
  database connection until Postgres answers (up to a minute — the first
  connection can race the container's network setup, which shows up as
  `failed to resolve host 'db'`), runs `migrate`, then `exec`s
  `gunicorn :8000` as `appuser`, bound to `127.0.0.1:8000` only — reachable
  from the host itself (the host Caddy), not from the network. It turns
  healthy once `/healthz/` answers 200, i.e. once migrations are done and
  the database answers.
- `celery` and `celery-beat` are only started once `web` is healthy, so no
  task ever runs against a half-migrated schema. Their own checks
  (`celery inspect ping`; the beat heartbeat file) turn green within about
  a minute.

Create the platform's first admin account:

```bash
docker compose exec web python manage.py createsuperuser
```

Then, in `/admin/`, set that user's `role` to `ADMIN` (via the custom
`User` model) if you want access to the `/admin/console/` platform-admin
dashboard described in `STATUS.md`/`FOR_JIM.md`.

Seed Celery Beat's periodic jobs (daily digest, weekly digest, overdue
reminders, tmp-file cleanup) — idempotent, safe to re-run after any
schedule change:

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
  renders, padlock is valid (Let's Encrypt, not self-signed), CSS loads
  (hashed filenames under `/static/css/`).
- Sign up with a real address you control; confirm the verification email
  arrives, that you **cannot** log in before clicking it (Part A5), and
  that the confirmation link logs you in.
- Send yourself a project invitation and check the link in the email is an
  absolute `https://i-connects.cossee.org/...` URL (that's `SITE_URL`
  working).
- `http://i-connects.cossee.org/` redirects to `https://`.
- Run the site through [Mozilla Observatory](https://developer.mozilla.org/en-US/observatory)
  or `testssl.sh` against the domain — HSTS, TLS version, and header grade
  should all come back clean given `prod.py`'s settings plus the Caddy
  headers from Part G.
- Confirm `/admin/` and `/admin/console/` are reachable only by
  authenticated staff/admin accounts (log out, hit both — expect a login
  redirect, not content).
- `docker compose stop web && docker compose up -d web`: the stop returns
  in well under 35 s and `docker compose logs web` shows gunicorn
  "Handling signal: term" — i.e. shutdowns are graceful, not SIGKILLed.
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
docker compose ps
docker compose logs --tail=50 web
```

Migrations run automatically on container start (`scripts/start-web.sh`
waits for Postgres, runs `migrate`, then `exec`s `gunicorn`); `collectstatic` ran during
`--build`, so a deploy that touches static files **must** include
`--build` — a plain `up -d` or `restart` serves the old assets. Watch the
first `docker compose logs` after every deploy for migration errors before
walking away, and confirm `docker compose ps` shows every service
`(healthy)` — `celery` and `celery-beat` are not recreated until `web` is.

**Try a branch before it merges** (a PR under review, a hotfix):

```bash
cd ~/i-connects
git status --short                       # must be empty apart from nothing; .env is ignored
git fetch origin
git checkout -B <branch> origin/<branch>  # local branch reset to the remote tip
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Don't `git pull origin <branch>` while on `main` — that tries to merge the
branch into your local `main` and stops with "divergent branches" as soon
as either side has moved. Once the PR merges, `git checkout main && git
pull origin main` and rebuild; the branch checkout can be deleted.

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

**Stop, start, restart:** all graceful. gunicorn receives SIGTERM directly
(35 s grace) and drains in-flight requests; Celery finishes in-flight tasks
(60 s); Postgres gets SIGINT for a fast, clean shutdown (60 s) so it never
has to run crash recovery on the next start. If `docker compose stop`
takes longer than those timeouts, something is wedged — read the logs
rather than raising the numbers.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop      # keep containers, stop processes
docker compose -f docker-compose.yml -f docker-compose.prod.yml start
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart web
```

**Tail logs:**

```bash
docker compose logs -f web            # gunicorn access + error log, Django tracebacks
docker compose logs -f celery celery-beat
sudo journalctl -u caddy -f           # host-level Caddy (Part G) — not a compose service on this host
```

Every service's log is capped at 5 × 10 MB by Docker (Part L), so
`docker compose logs` shows recent history only; anything that needs
keeping longer belongs in an external log sink.

**Postgres major versions:** the `db` service is pinned to `postgres:16`.
Never bump the major version in place — the data directory format is
version-specific and the container will refuse to start on the old data.
Upgrading means `scripts/backup.sh`, a new empty volume, then
`scripts/restore.sh` into the new major (or `pg_upgrade`), planned as its
own change. Redis 7 → 8 and Python/Node base-image bumps have no such
constraint.

**Review dependency updates:** `.github/workflows/ci.yml` runs the tests,
the CSS build, both compose renders and the image build on every push/PR,
and `.github/dependabot.yml` opens weekly PRs for `pip`, `npm`, and GitHub
Actions. Don't let Django/psycopg security-release PRs sit — those are
exactly what Part A4 flagged as already stale once.

---

<a id="part-k"></a>
## Part K — Backups & disaster recovery

**What to back up.** Everything durable is in two Docker volumes:
`i-connects_postgres_data` (the database — users, projects, tasks,
sessions, the beat schedule) and `i-connects_media_data` (`MEDIA_ROOT`;
today only scratch files under `tmp/`, but any future upload lands here).
Redis holds only in-flight queue state and is not backed up. Static files
are rebuilt from the image. `.env` is not in any backup on purpose — keep
it in whatever secret store the team uses, and treat losing it as a
rotation event (Part M), not a restore event.

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
nothing is read from `.env`; the media tarball is taken by a one-off
container on the web image, so it works even while `web` is down.

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

**Persistence drill** — the companion check for the live volumes rather
than the backups: `PERSISTENCE_DRILL.md` plants canary data, SIGKILLs the
stack, recreates the containers and (optionally) reboots the host, and
diffs what survived. Run it after any change to the compose volumes.

**Rebuilding from nothing** (lost host): Parts B–G on the new machine,
`docker volume create` both data volumes (Part H), restore the latest dump
and media tarball with `scripts/restore.sh` *before* the first `up` has run
`migrate` — or run `up`, let it migrate an empty database, and restore over
it; `--clean` handles either — then Part I. DNS (Part D) is the long pole:
change the A record early.

**Disk snapshots** (whatever hypervisor/host provider `cossee-web-server`
runs on) are the fastest full-system recovery path if available — schedule
daily snapshots with a 7+ day retention on the boot/data disk as a
complement to, not a replacement for, the `pg_dump` backups above (a
snapshot restores the whole VM state; a `pg_dump` restores just the data
into a fresh deploy).

---

<a id="part-l"></a>
## Part L — Monitoring, health, and log rotation

**Health checks.** Every service has one (see the table at the top).
`docker compose ps` shows the current verdict; the last five probe results,
with their output, are in

```bash
docker inspect --format '{{json .State.Health}}' i-connects-web-1 | jq
```

(`python3 -m json.tool` if `jq` isn't installed.) What each check means:

| Service | Check | Red means |
| --- | --- | --- |
| `web` | `curl` `/healthz/` with `Host: <first DJANGO_ALLOWED_HOSTS entry>` | gunicorn isn't answering, or it is but Postgres isn't (503), or `DJANGO_ALLOWED_HOSTS` is wrong (400) |
| `celery` | `celery inspect ping` addressed to this worker's own node | the worker process is alive but not consuming — a stuck task, a hung DB call, a lost broker connection |
| `celery-beat` | heartbeat file modified in the last minute (the scheduler touches it after every tick, at least every 5 s) | the scheduler is wedged or has lost the database |
| `db` | `pg_isready` over TCP | Postgres isn't accepting connections |
| `redis` | authenticated `PING` | Redis is down, or `REDIS_PASSWORD` doesn't match what it was started with |

**Unhealthy containers are not restarted by Docker.** A failing health
check only shows up in `docker compose ps` and gates `depends_on`;
`restart: unless-stopped` acts on process exit, not on health. The repo's
`scripts/restart-unhealthy.sh` restarts every container of this project
whose check is failing. Run it from cron every two minutes, as `deploy`
(`mkdir -p ~/logs` first):

```cron
*/2 * * * * cd ~/i-connects && scripts/restart-unhealthy.sh >> ~/logs/restart-unhealthy.log 2>&1
```

Each restart is one line in that log and honours the service's graceful
stop settings. A container that gets restarted repeatedly is a bug to
chase in `docker compose logs`, not something to leave to the cron job.

**Uptime.** Point an external uptime check (whatever your org already uses
— even a simple cron `curl` + alert is fine to start) at
`https://i-connects.cossee.org/healthz/` so a database outage is caught
too, not just a dead proxy. It answers `200` with `{"status": "ok", ...}`
or `503`, is unauthenticated, and is never cached.

**Container logs** grow unbounded under Docker's default `json-file`
driver. `docker-compose.prod.yml` caps every service at 5 × 10 MB via the
shared `x-logging` anchor; leave it in place (or set the same `log-opts`
globally in `/etc/docker/daemon.json`). gunicorn's access log goes to the
`web` container log as well, so request-level history is there, briefly.

**Resource limits.** `web` (1 GB, 1.5 CPUs), `celery` (1 GB), `celery-beat`
(256 MB) and `redis` (512 MB, with a 256 MB `maxmemory` inside) are capped
so a leak takes down one container, not the host; Postgres is uncapped but
gets a 256 MB `/dev/shm`. A container killed at its limit shows
`OOMKilled: true` in `docker inspect` and restarts by policy. Revisit the
numbers after watching `docker stats` under real load.

**Disk space.** Postgres, the Redis append-only file, backups and old
image layers all grow — `df -h /` periodically, or alert on it.
`docker system df` shows what Docker itself is holding; `docker image
prune` after deploys reclaims old build layers. `scripts/backup.sh` prunes
its own output after 30 days.

**Cert renewal.** The host-level Caddy (Part G) renews Let's Encrypt certs
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
   signed tokens (password reset links, invitation and confirmation links)
   — expected, not a bug.
3. `POSTGRES_PASSWORD` rotation: also `ALTER USER iconnects WITH PASSWORD
   '<new>'` inside the `db` container, then update `.env`'s
   `POSTGRES_PASSWORD` and `DATABASE_URL` together before recreating
   `web`/`celery`/`celery-beat`.
4. `REDIS_PASSWORD` rotation: update `.env` (the password and all three
   `redis://` URLs) and recreate the stack — the compose file reads the
   value from `.env` at `up` time. Queued tasks survive: the queue is on
   the `i-connects_redis_data` volume, not in the container.
5. Any SMTP/Resend/SES credential: rotate at the provider, update `.env`.
6. After any rotation:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```
   (no `--build` needed for secret-only changes — compose recreates the
   containers whose environment changed with the new values, gracefully.)
7. Check `/my/activity/` and `/projects/<id>/activity/` (the audit log) and
   Django admin's read-only audit view for anything suspicious in the
   affected window.

---

<a id="part-n"></a>
## Part N — Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `docker compose up` fails immediately with `required variable REDIS_PASSWORD is missing a value` | `REDIS_PASSWORD` unset or empty in `.env` | Set it (Part F) and make `REDIS_URL` / `CELERY_*` carry the same password. This is a deliberate guard — see Part F's note. |
| `external volume "i-connects_postgres_data" not found` (or `..._media_data`) | The data volumes are `external: true` and were never created on this host | `docker volume create` both names — Part H, first block. Never work around this by removing `external: true`: it is what stops `down -v` from deleting the database. |
| `docker compose up` fails with `failed to bind host port 127.0.0.1:8000/tcp: address already in use` | Something on the host already owns loopback port 8000. Three usual suspects: a `web` container from an *earlier* compose project (a checkout under another directory name, before the project name was fixed), a leaked `docker-proxy` left behind by a crash-looping container, or another app on the shared host | Find the owner: `sudo ss -ltnp 'sport = :8000'` and `docker ps -a --filter publish=8000 --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'`. **Old container** (a name not starting with `i-connects-`): `docker stop <name> && docker rm <name>` — then `docker volume ls`: if its project had its own `<oldname>_postgres_data`, that is where the real data is; copy it across per Part H *before* `up`. **`docker-proxy` with no container behind it** (the `docker ps -a --filter publish=8000` listing is empty, or `sudo tr '\0' ' ' < /proc/<pid>/cmdline` shows a `-container-ip` that no running container has — compare with `docker network inspect i-connects_default --format '{{range .Containers}}{{.Name}}={{.IPv4Address}} {{end}}'`): it is an orphan from an earlier crash-looping or failed start, and `sudo kill <pid>` from the `ss` output frees the port with no side effects. Restarting the Docker daemon also clears it but restarts every app on this shared host. Seen live on `cossee-web-server` after the first launch attempt. **Another app**: set `WEB_PORT=<free port>` in `.env`, point the Caddy site block at it, register it in `PORTS.md` (Part G3). In every case, remove the half-created container before starting again — `docker compose -f docker-compose.yml -f docker-compose.prod.yml rm -sf web` — then `up -d`. A container whose network setup failed once can be started again later without its network endpoint (next row). |
| `web` runs but every `start-web: database not reachable yet` line says `failed to resolve host 'db': Temporary failure in name resolution`, while `docker compose run --rm --no-deps web getent hosts db` resolves fine and `docker network inspect i-connects_default` lists `db` and `redis` but **not** `web` | The `web` container is not attached to the compose network. Seen live after an `up` whose port publishing failed ("address already in use"): compose kept re-starting that half-set-up container on later runs instead of recreating it | `docker compose -f docker-compose.yml -f docker-compose.prod.yml rm -sf web` then `up -d`. The fresh container joins the network normally. If it happens with no failed start in its history, `docker compose logs web` from the very first lines and `docker inspect i-connects-web-1 --format '{{json .NetworkSettings.Networks}}'`. |
| `docker compose up` ends with `dependency failed to start: container i-connects-web-1 is unhealthy` | `web`'s health check (`/healthz/` with the site `Host` header) is failing, so `celery`/`celery-beat` were not started | `docker inspect --format '{{json .State.Health}}' i-connects-web-1` shows the last probe outputs. `migrate` still running or failed → `docker compose logs web`. A `400` means `DJANGO_ALLOWED_HOSTS` is empty or malformed (the check uses its first entry). A `503` means gunicorn is up but cannot reach Postgres. Fix, then `up -d` again. |
| `docker compose ps` shows a service `(unhealthy)` but it keeps running | Docker never restarts on health status alone | Expected; the Part L cron (`scripts/restart-unhealthy.sh`) restarts it within two minutes. Read the health log (`docker inspect` as above) for the cause. |
| `web` log shows `start-web: database not reachable yet: ...` and `retrying in 2s (Ns left)` | Postgres isn't answering yet — normal for a few seconds after a reboot or a fresh `up`, since the first connection can race the container's network setup (`failed to resolve host 'db'`) | Expected briefly; `web` turns healthy on its own. If it runs the full 60 s and exits with `giving up`, the problem is persistent: `docker compose logs db` (is Postgres itself up?), then test name resolution from a throwaway container on the same network, `docker compose run --rm --no-deps web getent hosts db`. A hit means DNS works and the database is the problem; `Temporary failure in name resolution` means Docker's embedded DNS (127.0.0.11) isn't answering inside containers on this host — check `docker network inspect i-connects_default --format '{{range .Containers}}{{.Name}} {{end}}'` lists both, `docker compose run --rm --no-deps web cat /etc/resolv.conf` shows `nameserver 127.0.0.11`, and `cat /etc/docker/daemon.json` for `"iptables": false` or a custom `dns` entry, which is a host-level setting to raise with the host admin. |
| After a host reboot, `celery` or `celery-beat` restart a few times before settling | Docker restarts containers by policy at daemon start and ignores `depends_on`, so they can come up before Postgres | Expected and self-healing within a few seconds. If still looping after a minute, `docker compose logs db`. |
| `web` exits with code 127 and the log shows `sh: 3: --bind: not found` | The `web` command was written as a multi-line folded YAML string; YAML kept the line breaks and the shell ran each line as its own command | The committed files use `command: ["/app/scripts/start-web.sh", ...]` — a plain list. Never rewrite it as a `>` block; CI renders both stacks and fails on any command containing a newline. |
| Caddy logs `no such host` / `dns problem` | A-record not propagated | `dig +short i-connects.cossee.org`; wait or fix DNS (Part D). |
| Browser: `ERR_CONNECTION_REFUSED` on :443 | UFW/firewall blocks 443 | `sudo ufw status`; confirm `443/tcp` is allowed (Part B4). |
| Browser: `502 Bad Gateway` from Caddy | Nothing listening on `127.0.0.1:8000` | `docker compose ps` — `web` is down or still starting; `docker compose logs web`. Or the site file proxies to the wrong port: `cat /etc/caddy/PORTS.md`. |
| Django: `DisallowedHost` | `DJANGO_ALLOWED_HOSTS` mismatch | Must contain `i-connects.cossee.org` exactly (Part F). |
| Infinite redirect loop | `SECURE_SSL_REDIRECT=True` but `X-Forwarded-Proto` not forwarded | Confirm the `header_up X-Forwarded-Proto {scheme}` line is still present in `/etc/caddy/sites/i-connects.caddy` (Part G). |
| `relation "..." does not exist` | Migrations didn't run | `docker compose exec web python manage.py migrate`, then find out why the start-up `migrate` didn't — it runs on every `web` start. |
| Static files 404, or a page 500s with `Missing staticfiles manifest entry` | The running image predates the static change (or a template references a static file that doesn't exist) | `collectstatic` runs at image build: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`. For the manifest error, the named file is missing from `static/` — fix the template or add the file, then rebuild. |
| `docker ps` shows `0.0.0.0:5432->...` or `0.0.0.0:6379->...`, or `web` on `0.0.0.0:8000` | Someone edited the `ports:` overrides in `docker-compose.prod.yml` without `!reset`/`!override`, or ran the dev file alone | Part A7. `docker compose ... config` shows what will actually be published; fix and `up -d` (recreation is what takes effect). UFW is not protecting these ports meanwhile — `docker stop` them if you can't fix it immediately. |
| Cert stuck on staging / rate-limited | Repeated failed issuance attempts | Fix the underlying DNS issue first, then `caddy-reload` again. Let's Encrypt rate limits are per-domain-per-week — don't loop this; if truly stuck, whoever manages the host Caddy needs to clear its stored account/cert state, which affects every app behind it. |
| Signup says "check your inbox" but no confirmation email arrives | Outbound email is misconfigured — and since confirmation is mandatory (Part A5), every new user is locked out | `docker compose logs web celery \| grep -i -E "smtp\|mail"`; test with `manage.py sendtestemail` (F1). Check `EMAIL_HOST_PASSWORD` is an App Password, and `DEFAULT_FROM_EMAIL` has its angle brackets. |
| Login says "Please confirm your email address before signing in" | Expected for an account that never clicked its confirmation link | The login page links to a resend-confirmation form (per-IP throttled). An admin can also set `email_confirmed_at` on the user in `/admin/` if the address is known to be theirs. |
| Gmail SMTP: `535 5.7.8 Username and Password not accepted` | Using the account's normal password instead of an App Password, or 2-Step Verification isn't on | Re-check Part F1 — `EMAIL_HOST_PASSWORD` must be the 16-character App Password from `myaccount.google.com/apppasswords`, which doesn't exist until 2-Step Verification is enabled. |
| Gmail account has no "App passwords" option under Security | Workspace org policy disables them | If this is a University of Alberta Workspace account rather than a personal one, an admin may need to allow App Passwords, or the account may require OAuth2 for SMTP instead — this isn't a config mistake on your end. |
| Gmail-sent mail lands in spam, or `From` gets silently rewritten | `DEFAULT_FROM_EMAIL` doesn't match the authenticated Gmail address/alias | Set `DEFAULT_FROM_EMAIL` to the same address as `EMAIL_HOST_USER` (or a verified Gmail "Send mail as" alias), per Part F1. |
| Recipient's mail server bounces `550 5.7.30 ... DKIM authentication didn't pass` (affects *every* outgoing email — confirmation, password reset, invitations — not just one type) | Almost always `DEFAULT_FROM_EMAIL` is missing its angle brackets. It must be `Display Name <address@example.com>`; written as `Display Name address@example.com` (no `<...>`), the whole string *including the space* is parsed as the address, which then doesn't match the SMTP-authenticated account (`EMAIL_HOST_USER`) — Gmail can't align the two and rejects the message. `manage.py check` (which `manage.py migrate` also runs) catches this at deploy time as `core.W001`. Only if the format is already correct and this persists: the sending account may be a Google Workspace domain that needs DKIM signing turned on by an admin. | Fix the format first: `DEFAULT_FROM_EMAIL=i-connects <you@gmail.com>`, matching `EMAIL_HOST_USER` exactly, then recreate `web`/`celery` (`up -d`) and re-test. Only if that's already correct: for a Workspace domain, enable it under Admin console → Apps → Google Workspace → Gmail → Authenticate email, then publish the generated TXT record at the domain's DNS. |
| Invitation/join-request/digest email link is broken — e.g. `http:///invites/<code>/` (empty host) | `SITE_URL` isn't set. Those emails are sent from Celery tasks with no HTTP request to build an absolute URL from, so they fall back to `SITE_URL`; left unset it silently degrades to a bare relative path, which the recipient's mail client then mangles into a schemeless-host link | Set `SITE_URL=https://i-connects.cossee.org` in `.env` (Part F), `up -d` so `web` and `celery` pick it up, then send a fresh invitation to confirm the link is now absolute. |
| `manage.py check --deploy` warns about `SECRET_KEY` or `ALLOWED_HOSTS` | `.env` not actually loaded, or `DJANGO_SETTINGS_MODULE` wrong | `docker compose exec web env \| grep DJANGO`; confirm it reads `config.settings.prod`. |
| Container runs as root (`docker compose exec web id` shows `uid=0`) | Running a stale image | `up -d --build` (not just `up -d`); the `Dockerfile` switches to `appuser` (Part A3). |
| `docker compose stop` takes 30–60 s | A service is using its full grace period: a long Celery task finishing, or Postgres waiting out a client | Expected up to the configured limits (Part J). Longer than that, or every time, means something is wedged — `docker compose logs` for the slow service. |
| `docker compose down -v` didn't delete the database | Working as designed | The data volumes are `external: true`. Part O has the explicit, separate command if you really mean to. |

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
longer exists. Remove the two cron entries (Parts K and L) from `deploy`'s
crontab as well.

To decommission the host entirely: take a final `scripts/backup.sh` run,
confirm both files landed off-host, then deprovision the server through
whatever infrastructure/hosting console manages `cossee-web-server`, and
release the DNS `A` record for `i-connects.cossee.org` once nothing points
at it.
