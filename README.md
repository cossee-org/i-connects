# i-connects Django MVP

Phase 1 scaffold of the i-connects Django 5.2 rewrite. This phase delivers a
runnable project skeleton only: one landing page at `/`, a minimal custom
`User` stub, and all infra wired up (Postgres, Redis, Celery, Tailwind +
DaisyUI, HTMX, Alpine.js). Models, views, and business logic are introduced
in later phases.

## Stack

- Django 5.2, Django REST Framework
- HTMX 2.x + Alpine.js 3.x + SortableJS (via CDN)
- Tailwind CSS 3.x + DaisyUI 4.x (compiled to `static/css/output.css`)
- PostgreSQL 16, Redis 7
- Celery 5 + `django-celery-beat`
- `django-allauth` (ORCID OAuth wired in later)
- `django-crispy-forms` + `crispy-bootstrap5`
- Python 3.12, gunicorn

## Run with Docker (recommended)

```bash
cp .env.example .env
docker compose build
docker compose up
```

Then visit <http://localhost:8000/>.

The first `docker compose up` will:
1. Start Postgres and Redis.
2. Run `python manage.py migrate` inside the `web` container.
3. Serve gunicorn on port 8000 with `--reload`.
4. Start `celery` and `celery-beat` workers.

To create a superuser:
```bash
docker compose exec web python manage.py createsuperuser
```

## Run locally without Docker

Prerequisites: Python 3.12, Node 20+, a running Postgres 16, a running Redis 7.

```bash
# 1. Python env
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Environment
cp .env.example .env
# Edit .env: change DATABASE_URL and REDIS_URL hostnames from "db"/"redis"
# to "localhost".

# 3. Tailwind CSS
npm install
npm run build:css        # one-off
# or: npm run watch:css  # rebuild on change

# 4. Django
python manage.py migrate
python manage.py runserver
```

Then visit <http://localhost:8000/>.

In a separate shell, start Celery if you need it:
```bash
celery -A config worker -l info
celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Project layout

```
iconnects-django/
  config/              Django settings, URL, WSGI/ASGI, Celery
    settings/
      base.py          Common settings
      dev.py           DEBUG=True, console email
      prod.py          DEBUG=False, whitenoise, SECRET_KEY from env
  apps/                All Django apps, namespaced as `apps.<name>`
    accounts/          Custom User stub (Phase 1 placeholder)
    core/              Landing page at `/`
    projects/ tasks/ roles/ comments/ notifications/
    notes/ forms_builder/
  templates/           Project-level templates (base.html, core/home.html)
  static/
    css/input.css      Tailwind directives (source)
    css/output.css     Compiled CSS (checked-in placeholder)
    js/                Empty, populated in later phases
  cron/                Empty, populated in Phase 4 (Celery tasks)
  tailwind.config.js   Tailwind + DaisyUI config
  postcss.config.js
  package.json         Tailwind + DaisyUI only
  Dockerfile           Two-stage: Node (CSS build) -> Python 3.12 runtime
  docker-compose.yml   web, db, redis, celery, celery-beat
  requirements.txt
  .env.example
  manage.py
```

## Verification

```bash
python manage.py check
python manage.py makemigrations
```

Both commands should complete without errors. `makemigrations` creates the
initial `accounts` migration for the custom `User` model.

## What's next

- Phase 2: flesh out `accounts.User`, add ORCID via allauth.
- Phase 3A-E: data models + views for projects, tasks, roles, comments,
  notifications, notes, forms_builder.
- Phase 4: Celery task definitions under `cron/` (email digests, overdue
  reminders).
