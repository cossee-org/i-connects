# What's been built — Apr 23, 2026

Brief status report for testing against the i-CONNECTS Django MVP
(https://github.com/itchyshin/iconnects-django).

## To test locally

```bash
git pull origin main
cp .env.example .env   # if you haven't already
docker compose up --build
# open http://localhost:8000
```

### Quick way: load demo data (recommended first)

One command creates three demo users, a seeded project, and a mix of
approved / pending / open tasks so the app looks real when you sign in:

```bash
docker compose exec web python manage.py seed_demo
```

Sign in with any of:
- `alice@iconnects.test` (project manager — sees "Pending your approval")
- `bob@iconnects.test` (contributor — has open tasks + approved contributions)
- `cara@iconnects.test` (contributor — same)

All three use password `demopass`. The command is idempotent — running
twice is safe.

### Or: start fresh with your own superuser

```bash
docker compose exec web python manage.py createsuperuser
```

---

## What's new since the initial MVP

### 1. i-CONNECTS brand styling
Every page now wears the i-CONNECTS skin — navy `#323895`, coral `#f03d30`,
lavender surfaces, Inter font, logo in the top bar. Also a new public
landing page at `/` with a hero, feature cards, and "how it works" steps.

### 2. ContributionRecord ledger (the core differentiator)
Tasks now carry a CRediT-aligned contribution type. When a project manager
*approves* a completed task, an **immutable** `ContributionRecord` row is
written for each user behind the assigned member. The ledger refuses
updates and deletes at the DB level — not even admins can edit it.

Two new pages show it off:
- `/projects/<id>/contributions/` — members × CRediT-type matrix + recent
  activity, visible to every project member (PM sees all; members see
  their own project view).
- `/accounts/profile/` — "My contributions" section with per-type
  counts and a list of recent contributions across all projects.

### 3. Audit log
Every governance-relevant action is recorded in an append-only
`AuditLog` (same immutability as ContributionRecord):
- auth: login / logout / login failed / signup / password change / password reset
- projects: create / update / delete / invitation sent-accepted-declined, member add/remove, privilege change
- roles: create / delete / assign / unassign
- tasks: create / update / delete / complete / approve / reject

Two user-facing views:
- `/my/activity/` — your own audit trail
- `/projects/<id>/activity/` — project-scoped, PM-only

Plus the standard Django admin has a read-only view for platform admins.

### 4. Account lockout (WCAG-style security)
After **10 failed sign-in attempts in 15 minutes**, the account is locked
and the user is emailed a one-time unlock link (24h expiry, SHA-256-hashed
at rest). While locked, even the correct password is rejected with a
clear "check your email" message. Clicking the email link clears the
lockout (audit-logged).

### 5. Accessibility (WCAG 2.1 AA baseline)
Skip-to-content link, visible focus rings on every interactive element,
`aria-current` on active tabs, `aria-live` on messages, alt text on all
images, `aria-label` on icon-only buttons, `scope="col"` on data tables,
`role="main"` / `role="banner"` / `role="contentinfo"` landmarks.
One known gap: the SortableJS kanban has no keyboard alternative — a
known issue flagged for v2.

### 6. CSV export of contributions
"Export CSV" buttons on the project contributions page (PM-only) and on
the "My contributions" section of the profile. Columns: project,
user_name, user_email, user_orcid, contribution_type, task, closed_at
(ISO-8601), verified_by. Streams server-side so exports scale to
thousands of rows. Designed to be paste-ready into an authorship /
CRediT statement spreadsheet.

### 7. Role-differentiated dashboard widgets
The "My Projects" landing page (`/projects/mine/`) now surfaces two
attention-oriented widgets above the project grid:
- **My active tasks** — your 5 most-due open assignments across all
  projects.
- **Pending your approval** — for project managers only: completion
  events awaiting your sign-off. Clicking jumps straight to the task
  detail where you approve or reject.

Widgets only appear when they have content, so users with nothing
pending see the familiar pure project grid.

---

## Golden-path test checklist

1. **Landing + signup + login**
   - Visit `/` as a logged-out user — landing page renders.
   - Sign up at `/accounts/signup/` — you're auto-logged in; welcome email in console.
   - Log out via user menu; log back in.
   - After login, the "My Projects" page now shows **My active tasks** and
     **Pending your approval** widgets when you have anything in flight.
2. **Project + tasks**
   - Create a project; add a kanban column.
   - Create a task — note the new "Contribution type" select (Investigation / Data curation / Validation / Writing / Project administration / Other).
   - Mark the task done (the assignee or you).
   - Approve the completion from the task detail view.
3. **Contribution ledger + CSV export**
   - Open `/projects/<id>/contributions/` — the matrix shows 1 in the column matching the task's contribution type.
   - As the PM, click "Export CSV" on the same page — downloads a spreadsheet of every contribution on this project.
   - Open `/accounts/profile/` — the "My contributions" section shows the pill + recent entry.
   - Click "Export CSV" next to "My contributions" — downloads your own contribution sheet across all projects.
4. **Audit log**
   - Open `/my/activity/` — see your login, signup, project-created, task-created, task-completed, task-approved.
   - As the PM, open `/projects/<id>/activity/` — project-scoped view.
   - A non-PM member hitting the same URL should get a 403.
5. **Account lockout**
   - Log out. Submit wrong credentials for your own account 10 times.
   - Check the email console: one lockout email with a link.
   - Try to log in with the correct password — rejected with "locked" message.
   - Click the link in the email — unlocked. Log in normally.
6. **Accessibility spot-checks**
   - Tab through the landing page — the skip-link should appear on first Tab.
   - Tab around the top navbar — coral focus ring visible on every control.
   - Zoom to 200% — layout holds without horizontal scroll.

---

## Known gaps (explicit, so you don't chase them)

### Paused pending external dependencies
1. **ORCID OAuth** — routes exist, but login 500s without an ORCID client id/secret and a `SocialApp` DB row. Needs the UA to obtain ORCID developer credentials.
2. **Production deployment** — local Docker boots clean, but no staging server exists yet. Needs UA IT to provision a subdomain, TLS cert, and backup policy.

### v2 work explicitly deferred in the Ultra Plan
- Full 15-item CRediT taxonomy (we ship the simplified 6-item subset).
- XLSX / ZIP export (CSV is shipped).
- Auto-formatted prose CRediT author contribution statement.
- Cross-project merit profiles / public contributor pages.
- Trust/reliability scoring (v3; requires careful anti-gaming design).
- Keyboard-only alternative to the kanban drag-drop.

### Open design question
- **Accent coral `#f03d30`** is the working value (resolved from the logo since the brand sheet's `#231f20` is clearly a typo). If your designer confirms a different exact hex, it's a one-line change in `tailwind.config.js`.

---

## What I'd suggest you do next

1. **Skim the golden-path list** (10–15 min) to make sure the flows match your mental model.
2. **Write back one of:**
   - "Ready to invite a few lab members to try it on a real project"
   - "Change these N things first" (specific list)
   - "Proceed with ORCID/server dependencies in parallel"
3. Anything that *blocks on you*: UA IT ticket for server + ORCID credentials, designer confirmation of the coral hex.
