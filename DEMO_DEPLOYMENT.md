# Running a demo locally

How to bring i•CONNECTS up on your own machine, load a demo project, and
sign in — for trying the app out, showing it to someone, or checking a
change before it ships. Nothing here is fit for a public host; for that,
see `DEPLOYMENT.md` (and the last section below).

There are two ways to run it. **Docker is the one to use** unless you have a
reason not to: it is the same stack production runs, and it needs nothing on
your machine but Docker itself.

| | Docker (A) | Without Docker (B) |
|---|---|---|
| You install | Docker Desktop / Engine with Compose v2 | Python 3.12, Node 20+, Postgres 16, Redis 7 |
| Starts | Postgres, Redis, web, Celery worker, Celery beat | whatever you start by hand |
| Site | <http://localhost:8000/> | <http://localhost:8000/> |

---

## A. With Docker

### A1. Start the stack

From the repository root:

```bash
cp .env.example .env
docker compose up -d --build
```

The defaults in `.env.example` are already right for a local run — the
`db` and `redis` hostnames are the Compose service names, `DEBUG` is on, and
Redis has no password. Leave `REDIS_PASSWORD` empty; only the production
compose file needs it.

The first build takes a few minutes. Then wait for every service to report
`(healthy)`, which takes about a minute:

```bash
docker compose ps
```

`web` turns healthy only once `migrate` has finished, and `celery` and
`celery-beat` wait for that, so a fresh database is always fully migrated
before anything runs against it. <http://localhost:8000/healthz/> answers
`{"status": "ok"}` when the app can reach Postgres.

### A2. Load the demo data

```bash
docker compose exec web python manage.py seed_demo
```

This creates three users, a public project ("Demo: bird song
meta-analysis") with a Kanban column, and eight tasks taken through the
mark-done → close lifecycle so the contributions pages have real credit to
show. It is idempotent: running it again changes nothing that is already
there. See [Signing in](#signing-in) for the accounts.

`seed_demo` refuses to run when `DEBUG` is off, so it cannot be pointed at
production by mistake.

### A3. Day to day

Follow the server's output, including the emails it prints (see
[Signing up through the site](#signing-up-through-the-site)):

```bash
docker compose logs -f web
```

Stop the stack, keeping the database, and start it again:

```bash
docker compose stop
docker compose up -d
```

Apply migrations after pulling new models:

```bash
docker compose exec web python manage.py migrate
```

Copy commands one block at a time and paste them without trailing notes:
macOS's default shell, zsh, does not treat `#` as a comment at the prompt,
so `cmd  # note` passes `#` and `note` to the command as arguments.

The `web` container mounts your checkout and serves it with `runserver`, so
code edits take effect on save. Migrations are the exception: they run only
when the container starts. After a `git pull` that touches a model, run the
`migrate` line above — until you do, every page is replaced by a note naming
the outstanding migrations (a `DEBUG`-only page).

Stylesheets are the other exception. The mounted checkout hides the CSS the
image built, so the site uses the committed `static/css/output.css`. If you
add Tailwind classes to a template, rebuild it on the host with
`npm install && npm run build:css` (or `npm run watch:css` while you work).

### A4. Start over

```bash
docker compose down -v
```

This **deletes the local database** (the `i-connects_postgres_data` volume)
along with the containers. `docker compose up -d --build` then gives you an
empty, freshly migrated instance; re-run `seed_demo` to repopulate it.

---

## B. Without Docker

Prerequisites: Python 3.12, Node 20+, Postgres 16 and Redis 7, both running
on their default ports.

**No Postgres or Redis installed?** Docker can run just those two while
Django runs on your machine. Their ports are published and the database
already has the role and password `.env.example` expects, so skip B1 and
start them with:

```bash
docker compose up -d db redis
```

Whichever way they run, the `.env` change in B2 is still required. Django
on your machine cannot see the Docker-only hostnames `db` and `redis`, and
fails with `failed to resolve host 'db'` until they say `localhost`.

### B1. Database

Create the role and database `.env.example` expects (as a Postgres
superuser, e.g. `sudo -u postgres psql` on Linux, `psql postgres` with
Homebrew):

```sql
CREATE ROLE iconnects LOGIN PASSWORD 'iconnects' CREATEDB;
CREATE DATABASE iconnects OWNER iconnects;
```

`CREATEDB` is only for the test suite, which creates its own database.

### B2. Environment and dependencies

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

In `.env`, point the database and Redis at your machine instead of at the
Compose service names — change `@db:5432` to `@localhost:5432` and every
`redis://redis:6379` to `redis://localhost:6379`:

```dotenv
DATABASE_URL=postgres://iconnects:iconnects@localhost:5432/iconnects
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

Django reads `.env` itself, so there is nothing to `export`.

### B3. Build, migrate, seed, run

```bash
npm install
npm run build:css

python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

To rebuild the CSS on every template change instead, run
`npm run watch:css` in a shell of its own. The site is up at
<http://localhost:8000/>. Pages and sign-in work without
Celery. To also run background jobs (invitation and digest emails,
scheduled reminders), start these in two more shells, with the virtualenv
active:

```bash
celery -A config worker -l info
celery -A config beat -l info --scheduler apps.core.celery_scheduler:HeartbeatDatabaseScheduler
```

After a `git pull`, run `python manage.py migrate` again.

---

## Signing in

### Demo accounts

After `seed_demo`, sign in at <http://localhost:8000/accounts/login/> with
any of these. All three use the password **`demopass`**.

| Email | Role in the demo project |
|---|---|
| `alice@iconnects.test` | Lead (project manager) — owns the project, sees the review queue |
| `bob@iconnects.test` | Contributor — has an open task and closed, credited work |
| `cara@iconnects.test` | Contributor — has a task marked done and waiting on Alice |

Sign in as Alice and Bob in two different browsers (or a private window) to
see the same project from the lead's and a contributor's side.

### Your own admin account

Under Docker, prefix both commands below with `docker compose exec web`.

```bash
python manage.py createsuperuser
```

Sign-in refuses an account whose email address has not been confirmed, and
`createsuperuser` does not confirm it. Mark it confirmed yourself, using the
address you just entered:

```bash
python manage.py shell -c "from django.utils import timezone; from apps.accounts.models import User; User.objects.filter(email='you@example.com').update(email_confirmed_at=timezone.now())"
```

The account can then sign in normally and reach `/admin/`.

### Signing up through the site

The sign-up form works too. Locally nothing is actually mailed: the
development settings print every email to the server's output instead. The
confirmation link appears in the `runserver` shell, or in
`docker compose logs web` under Docker — open it in the browser to confirm
the account. Emails sent by Celery tasks (invitations, join requests,
digests) appear in the `celery` worker's output the same way.

---

## Troubleshooting

**"Please confirm your email address before signing in."** The account has
no confirmed email — see [Your own admin account](#your-own-admin-account).
Demo accounts created by an older `seed_demo` are repaired by running
`seed_demo` again.

**`docker compose up` fails with "port is already allocated".** Something on
your machine already uses 8000, 5432 or 6379 — often a locally installed
Postgres or Redis. Stop it while the Docker stack runs, or move the
container's host port in a local, uncommitted `docker-compose.override.yml`
(Compose reads it automatically). `!override` is needed because Compose
otherwise *adds* override ports to the original ones:

```yaml
services:
  db:
    ports: !override ["5433:5432"]
```

**`web` never turns healthy.** `docker compose logs web` shows why; the
usual cause is a migration error. Startup waits up to 60 s for Postgres and
retries the connection, so "database not reachable yet" lines during the
first few seconds are expected.

**The page looks unstyled.** `static/css/output.css` is missing or stale —
run `npm install && npm run build:css`.

**`failed to resolve host 'db'`** (or `'redis'`). Django is running
outside Docker with the Docker hostnames still in `.env`; change them to
`localhost` as in B2.

**`column … does not exist`.** Migrations are behind the code; run
`migrate` (A3 or B3).

---

## Hosting a demo on a VM

A demo that other people reach over the internet is a deployment, not a
local run: **follow `DEPLOYMENT.md`**, which covers `.env`, the external
data volumes, health checks, backups and the restart-unhealthy cron. For a
throwaway single-app VM that has no host-level Caddy of its own, the only
additions are:

1. A `caddy` service in a *local, uncommitted* override file, using a copy
   of the repo `Caddyfile` with the domain changed and the upstream set to
   `web:8000` (Docker DNS) instead of `127.0.0.1:8000`:

   ```yaml
   # docker-compose.caddy.yml -- add as a third -f file
   services:
     caddy:
       image: caddy:2
       restart: unless-stopped
       ports: ["80:80", "443:443"]
       volumes:
         - ./Caddyfile:/etc/caddy/Caddyfile:ro
         - caddy_data:/data
         - caddy_config:/config
       depends_on:
         web:
           condition: service_healthy
   volumes:
     caddy_data:
     caddy_config:
   ```

2. `DJANGO_ALLOWED_HOSTS` and `SITE_URL` set to the demo domain in `.env`.

Do not run `seed_demo` there: the production settings turn `DEBUG` off and
the command refuses, and its accounts share a published password.
