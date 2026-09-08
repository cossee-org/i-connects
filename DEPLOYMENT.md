# Production Deployment — i-connects

End-to-end instructions for a secure production launch on the dedicated
server, over SSH. Domain: **`i-connects.cossee.org`**. Target host:
`cossee-web-server`, Ubuntu 24.04.4 LTS (`noble`), kernel 6.8, x86_64.

Stack: Docker Compose (Django/gunicorn + Postgres 16 + Redis 7 + Celery +
Celery Beat), fronted by Caddy 2 for automatic Let's Encrypt TLS. This
mirrors the pattern in `DEMO_DEPLOYMENT.md`, but this guide is written for
the **real production deployment** and closes the gaps that doc
deliberately left open for a throwaway demo.

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

### A5. Require verified email before invitations/login trust it (closes H1)

`config/settings/base.py` sets `ACCOUNT_EMAIL_VERIFICATION = "optional"`,
and `AcceptOrDeclineInviteView` authorizes purely by string-matching
`request.user.email`. Anyone can sign up with an email they don't own and
hijack an invitation sent to that address before the real owner ever signs
up.

```diff
-ACCOUNT_EMAIL_VERIFICATION = "optional"
+ACCOUNT_EMAIL_VERIFICATION = "mandatory"
```

This requires outbound email to work (Part F, SMTP/Resend/SES) — verify
signup → confirmation email → login works end-to-end in staging before
flipping this in production, since a broken email path with `mandatory`
verification locks every new signup out.

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

### A7. Fix `ports: []` not actually closing db/redis, and add Redis auth (closes M4)

Two separate bugs, same symptom — `db` and `redis` reachable on `0.0.0.0`
in production.

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

**Bug 2 — no Redis password.** Even fully unpublished (internal-network
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
DEFAULT_FROM_EMAIL=i-connects <noreply@i-connects.cossee.org>

# smtp:
EMAIL_HOST=<your SMTP relay, e.g. University of Alberta's or a transactional provider's>
EMAIL_PORT=587
EMAIL_HOST_USER=<smtp username>
EMAIL_HOST_PASSWORD=<smtp password>
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
- `STATICFILES_STORAGE` is whitenoise's
  `CompressedManifestStaticFilesStorage`, so `collectstatic` **must** run
  before gunicorn starts — `docker-compose.prod.yml`'s `web` command
  already does this.
- Never commit `.env`. It's covered by `.gitignore` after Part A8; double
  check with `git status` before any commit from this checkout.

Lock down the file permissions since it holds every production secret:

```bash
chmod 600 .env
```

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

```diff
 services:
   web:
     command: >
       sh -c "python manage.py migrate --noinput &&
              python manage.py collectstatic --noinput &&
              gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3"
     volumes: []
     restart: unless-stopped
-    ports: []
-    expose:
-      - "8000"
+    ports:
+      - "127.0.0.1:8000:8000"
   ...
-  caddy:
-    image: caddy:2
-    restart: unless-stopped
-    ports:
-      - "80:80"
-      - "443:443"
-    volumes:
-      - ./Caddyfile:/etc/caddy/Caddyfile:ro
-      - caddy_data:/data
-      - caddy_config:/config
-    depends_on:
-      - web
-
-volumes:
-  caddy_data:
-  caddy_config:
```

Binding to `127.0.0.1` rather than `0.0.0.0` means only processes on the
host itself — the host Caddy — can reach gunicorn on `:8000`; the outside
world can't hit it directly, with or without a firewall rule.

This repo's own `Caddyfile` (pointing at `demo.i-connects.cossee.org`) is
now unused on this host — either delete it or keep it only as the source
text for the site block in G3.

### G2. DNS

Covered in Part D — the A record must resolve before G3's `reload` can
issue a cert for it.

### G3. Add a site block to the existing host Caddyfile

Locate it, and confirm with whoever else's app it already serves before
editing a file they depend on too:

```bash
sudo systemctl status caddy
sudo find / -xdev -iname "Caddyfile" 2>/dev/null
```

Append (baseline security headers as defense-in-depth on top of Django's
own `SecurityMiddleware` — closes part of SECURITY_AUDIT M6):

```caddyfile
i-connects.cossee.org {
    encode zstd gzip

    header {
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), camera=(), microphone=()"
        -Server
    }

    reverse_proxy 127.0.0.1:8000
}
```

Validate before reloading — a bad edit here breaks every app behind this
Caddy, not just this one:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy   # graceful — reload, not restart; no dropped
                               # connections; cert is fetched on first request
```

### If this app is ever the *only* thing on a host

The original per-repo Dockerized `caddy:` service (the one G1 removes) is a
perfectly good, simpler default when nothing else shares the machine — one
`docker compose up`, TLS included, no host-level Caddy to coordinate with.
`cossee-web-server` isn't that kind of host, so this guide documents the
shared-host path as the one that actually applies here.

---

<a id="part-h"></a>
## Part H — Launch

```bash
cd ~/i-connects
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
docker compose logs --tail=100 web celery celery-beat
docker ps --format '{{.Names}}\t{{.Ports}}'   # confirm the state below
```

What this does (assuming Part G's `docker-compose.prod.yml` edits are
applied — no `caddy` service in this stack on this host):

- `web` runs `migrate` → `collectstatic` → `gunicorn :8000` as `appuser`
  (after Part A3), bound to `127.0.0.1:8000` only — reachable from the host
  itself (i.e., the host-level Caddy from Part G), not from the network.
- TLS and the public `:80`/`:443` edge are handled entirely by the existing
  host-level Caddy (Part G) — this app doesn't run or manage a reverse
  proxy of its own on `cossee-web-server`.
- `db` and `redis` are unreachable from the host network or the public
  internet once Part A7's `!reset []` fix is applied — verify with the
  `docker ps` line above; neither should show a `0.0.0.0:...` mapping.
  `redis` also requires the password from Part F/A7.
- `celery` and `celery-beat` start once `db` and `redis` pass their health
  checks.

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
curl -sI https://i-connects.cossee.org/ | grep -i strict-transport-security
docker compose ps                                        # every service healthy/running
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

Migrations and `collectstatic` re-run automatically on container start (the
prod `web` command chains them before `gunicorn`). Watch the first
`docker compose logs` after every deploy for migration errors before
walking away.

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

**Database backup (one-off):**

```bash
mkdir -p ~/backups
docker compose exec -T db pg_dump -U iconnects iconnects \
  | gzip > ~/backups/db-$(date +%F).sql.gz
```

**Nightly cron**, `crontab -e` as `deploy`:

```cron
0 3 * * * cd ~/i-connects && docker compose exec -T db pg_dump -U iconnects iconnects | gzip > ~/backups/db-$(date +\%F).sql.gz
30 3 * * 0 find ~/backups -name 'db-*.sql.gz' -mtime +30 -delete
```

(Nightly for 30 days locally; ship copies off-host — see below — for
anything you actually need to survive a lost disk.)

**Off-host copy** — cron dumps alone don't protect against losing the VM.
Sync `~/backups` to durable off-host storage on the same schedule (object
storage, another host via `rsync`/`rclone`, whatever your org already
uses). This guide intentionally doesn't hardcode a specific provider since
none was specified — pick one and wire it into the cron job above before
calling backups "done."

**Media files:** if user uploads accumulate under `MEDIA_ROOT`
(`/app/media` in the container), back those up too — `pg_dump` only covers
the database:

```bash
docker compose cp web:/app/media ~/backups/media-$(date +%F)
```

**Restore from a dump:**

```bash
gunzip -c ~/backups/db-YYYY-MM-DD.sql.gz \
  | docker compose exec -T db psql -U iconnects -d iconnects
```

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
  driver. Cap them — add to `docker-compose.prod.yml` under each service
  (or globally via `/etc/docker/daemon.json`'s `log-opts`):
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "5"
  ```
- **Uptime**: point an external uptime check (whatever your org already
  uses — even a simple cron `curl` + alert is fine to start) at
  `https://i-connects.cossee.org/` so an outage is caught before a user
  reports it.
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
| Infinite redirect loop | `SECURE_SSL_REDIRECT=True` but `X-Forwarded-Proto` not forwarded | The host Caddy's `reverse_proxy` sets it by default — confirm the site block in `/etc/caddy/Caddyfile` (Part G) wasn't edited to drop it. |
| `relation "..." does not exist` | Migrations didn't run | `docker compose exec web python manage.py migrate`. |
| Static files 404 | `collectstatic` didn't run | `docker compose exec web python manage.py collectstatic --noinput`. |
| Cert stuck on staging / rate-limited | Repeated failed issuance attempts | Fix the underlying DNS issue first, then `sudo systemctl reload caddy` again. Let's Encrypt rate limits are per-domain-per-week — don't loop this; if truly stuck, whoever manages the host Caddy needs to clear its stored account/cert state, which affects every app behind it. |
| `docker ps` shows `0.0.0.0:5432->...` or `0.0.0.0:6379->...` | Part A7's `!reset []` fix not applied, or applied but not rebuilt | Re-check the `docker-compose.prod.yml` diff, then `up -d` (recreation, not just editing the file, is what takes effect). This also means UFW was never protecting these — see Part B4's note. |
| New signups never get a verification email (post-A5) | SMTP/Resend/SES misconfigured | `docker compose logs web \| grep -i mail`; verify `.env`'s email block against Part F before flipping `ACCOUNT_EMAIL_VERIFICATION` back to `optional` as a stopgap. |
| `manage.py check --deploy` warns about `SECRET_KEY` or `ALLOWED_HOSTS` | `.env` not actually loaded, or `DJANGO_SETTINGS_MODULE` wrong | `docker compose exec web env \| grep DJANGO`; confirm it reads `config.settings.prod`. |
| Container runs as root (`docker compose exec web id` shows `uid=0`) | Part A3 not applied/rebuilt | Re-check the `Dockerfile` diff, then `up -d --build` (not just `up -d`). |

---

<a id="part-o"></a>
## Part O — Tear-down

Stop the stack but keep data:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

Wipe everything, including the database and Redis volumes — **irreversible,
confirm you have a current off-host backup (Part K) first**:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
```

Since Part G put this app behind the host's shared Caddy rather than a
Caddy this stack owns, also remove the `i-connects.cossee.org` block from
`/etc/caddy/Caddyfile` and `sudo systemctl reload caddy` — otherwise the
shared proxy keeps trying to route to a `127.0.0.1:8000` that's no longer
listening, and its error logs (and other apps behind it) get noisier for
no reason.

To decommission the host entirely: take a final `pg_dump` + media backup,
confirm both landed off-host, then deprovision the server through whatever
infrastructure/hosting console manages `cossee-web-server`, and release the
DNS `A` record for `i-connects.cossee.org` once nothing points at it.
