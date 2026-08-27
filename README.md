# i-CONNECTS Django

A free, scientist-focused collaboration platform for tracking who does what
in distributed research projects. This repo is the Django/HTMX rewrite of
the original Blitz.js prototype, built to run on University of Alberta
infrastructure for the Nakagawa/Lagisz lab.

See [FOR_JIM.md](FOR_JIM.md) for the current status report and golden-path
test checklist.

---

## Features

- **Projects, tasks, kanban boards** — create, invite, assign, complete,
  peer-verify, and approve. Review is opt-in per task ("Require review"),
  and a completed task is frozen until its completion is reversed.
- **CRediT contribution tracking** — a project opts in when it is
  created, its tasks carry one of CRediT's 14 roles, and closing a task
  credits the assignees who marked their part done (and had it reviewed,
  where the task has reviewers). Correctable by a project lead, and
  downloadable as a matrix or a list.
- **Audit log** — append-only record of auth, project, member, role, and
  task lifecycle events. Per-user and per-project activity views plus
  Django admin read-only surface.
- **Account lockout** — 10 failed sign-ins in 15 minutes locks the
  account; one-time email link unlocks. Auth signals ensure every
  failure is recorded, including ORCID flows.
- **CSV downloads of contributions** — a members × roles matrix or a
  row-per-contribution list, streamed to a spreadsheet for authorship /
  CRediT statements.
- **People discovery** — a directory of everyone on the platform,
  searchable by name, affiliation, city or ORCID, filterable by
  institution and country, and sortable by name, institution or join
  date. Each entry opens a researcher profile: name, pronouns, ORCID,
  institution, city, country, public projects, and CRediT contributions.
- **Role-differentiated dashboard** — "My active tasks" + "Pending your
  approval" widgets for authenticated users.
- **Forms builder** — JSON-schema-driven per-project custom forms
  (jsoneditor).
- **Notes** — per-project markdown notes (EasyMDE).
- **Notifications** — HTMX-polled badge + dropdown + list page; email
  channel wired via Django's `EMAIL_BACKEND`.
- **Accessibility** — WCAG 2.1 AA baseline (skip-link, focus rings,
  landmarks, aria-labels on icon buttons, scope attrs on data tables).

## Stack

- Django 5.2, Django REST Framework
- HTMX 2.x + Alpine.js 3.x + SortableJS (via CDN)
- Tailwind CSS 3.x + DaisyUI 4.x with the `iconnects` brand theme
- PostgreSQL 16, Redis 7
- Celery 5 + django-celery-beat
- django-allauth (ORCID OAuth — requires external credentials to activate)
- Python 3.12, gunicorn

## Run with Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
```

Visit <http://localhost:8000/>.

Create a superuser:
```bash
docker compose exec web python manage.py createsuperuser
```

### After pulling new code

The `web` service mounts the repo and serves it with `runserver`, so a
`git pull` swaps the code under the running server — but migrations only run
when the container starts. Pull anything that touches a model and the schema
is left behind, which used to surface as `column ... does not exist`. Apply
them without a restart:

```bash
docker compose exec web python manage.py migrate
```

If you forget, the site says so: while anything is outstanding every page is
replaced by a note naming the migrations and this command. That page is
`DEBUG`-only, and clears itself as soon as `migrate` finishes.

## Run locally without Docker

Prerequisites: Python 3.12, Node 20+, Postgres 16, Redis 7.

```bash
# Python env
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Environment
cp .env.example .env
# Edit .env — change DATABASE_URL and REDIS_URL hosts from "db"/"redis" to "localhost".

# Tailwind
npm install
npm run build:css          # one-off
# or: npm run watch:css    # live rebuild

# Django
python manage.py migrate
python manage.py runserver
```

In a second shell if Celery is needed:
```bash
celery -A config worker -l info
celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Tests

```bash
pytest -q
```

67+ tests covering auth flows, the task lifecycle, the contribution
ledger (with immutability guards), the audit log, account lockout, and
CSV export.

## Project layout

```
iconnects-django/
  config/
    settings/{base,dev,prod,test}.py
    urls.py / celery.py / wsgi.py
  apps/
    accounts/       Custom User, auth views, lockout, email helpers
    core/           Public landing page
    projects/       Project, ProjectMember, Invitation, ProjectPrivilege
    tasks/          Task, KanbanBoard, Milestone, TaskLog
    roles/          Role definitions and member-role assignments
    comments/       Comment threads on TaskLogs
    notifications/  In-app notification centre
    notes/          Markdown notes per project
    forms_builder/  Dynamic JSON-schema forms
    contributions/  ContributionRecord ledger + matrix / list downloads
    audit/          Immutable AuditLog with viewer
  templates/
    base.html
    _components/    page_header, empty_state, form_field, status_badge
    <per-app templates>
  static/
    css/input.css / output.css
    img/iconnects-logo.{png,jpg}
  STYLE_GUIDE.md    brand tokens, components, dos/don'ts
  AUDIT_GUIDE.md    audit action enum + wire-up conventions
  A11Y_GUIDE.md     the 10-check WCAG 2.1 AA protocol
  FOR_JIM.md        current status report + golden-path test checklist
```

## Open dependencies blocking production

- **ORCID OAuth credentials** — routes and provider are wired;
  provisioning a `SocialApp` row with UA-issued client id/secret turns
  the flow on. No code change needed.
- **UA server access** — Docker boots locally; production needs a
  subdomain, TLS, and backup policy from UA IT.
- **Designer confirmation of the accent coral hex** — currently
  `#f03d30` (resolved from the logo). A different hex is a one-line
  change in `tailwind.config.js` + `npm run build:css`.

---

Ultra Plan design document is upstream; this implementation follows its
§1–§15 scope for v1 with explicit deferrals for v2 features.
