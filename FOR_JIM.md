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

### 2. CRediT contribution tracking (the core differentiator)
A project picks how contributions are attributed when it is created —
"None" or the [CRediT taxonomy](https://credit.niso.org/) — and on a CRediT
project every task carries a **task type**, one of CRediT's 14 contributor
roles.

Closing a task is the ledger event. When a project lead completes it, a
`ContributionRecord` is written for each assignee who marked their own part
done; where the task carries reviewers, only for submissions a reviewer
signed off. Reopening the task withdraws that credit again, and closing it
writes it back.

The ledger is correctable. A lead can change the role a contribution was
recorded under, withdraw one, or enter one by hand for work the automatic
writer passed over — a person who did the task without ever being an
assignee. Hand-entered rows are marked as such, and survive the task being
reopened. Every correction is audit-logged.

Two pages show it:
- **Contribution Tracking** on the project (`/projects/<id>/contribution-tracking/`)
  — members × CRediT-role matrix, then every contribution listed with its
  task and who closed it. Visible to every project member.
- **Contributions** in the sidebar (`/contributions/`) — your own credit
  across every project, filterable by project.

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

### 6. CSV downloads of contributions
Both contributions pages hand over a CSV in either of two shapes:
- **List** — one row per contribution: project, user_name, user_email,
  user_orcid, contribution_type, task, closed_at (ISO-8601), verified_by.
- **Matrix** — the grid the page shows: members (or, on your own download,
  projects) down the side, all 14 CRediT roles across the top, counts in
  the cells and totals on both edges.

The module's downloads carry its project filter. The matrix is open to
every project member — it is what their page already shows them — while
the list stays with the leads, since it carries everyone's email and ORCID.
Both stream server-side, so exports scale to thousands of rows, and are
designed to be paste-ready into an authorship / CRediT statement.

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

### 8. Public researcher profile page (`/people/<id>/`)
Any signed-in member can view any other researcher's public profile —
identity, ORCID iD (linked out), institution, and their contribution
counts by CRediT type + recent contributions. Email is only shown when
viewing your own profile. Auto-linked from the project contributions
"Recent activity" list so you can click any name to see the researcher's
full record.

### 9. Admin console (`/admin/console/`)
For platform admins (`User.role == ADMIN`) only: an 8-tile dashboard
of system health (users total / active / locked, projects, tasks
open / overdue, contributions recorded) plus three activity columns —
recent signups, recent failed logins, recent lockouts. Links through to
the Django admin audit-log view for full forensic detail.

### 10. Rate limiting on signup + password reset
5 submissions per IP per 10 minutes on each of `/accounts/signup/` and
`/accounts/password/forgot/`. The 6th attempt returns HTTP 429 with a
"Too many requests" message. Counters use the existing audit log —
visible in `/my/activity/` for operators investigating abuse. Does NOT
affect the login flow (which has per-email lockout already).

### 11. Kanban keyboard alternative
The task detail page now has a "Move to column" select in the sidebar
under a "Move to column" disclosure. Keyboard users can tab to any task
card's title link → reach the detail page → change column from the
dropdown. Closes the last WCAG 2.1 AA gap flagged in the accessibility
audit.

### 12. GitHub Actions CI + Dependabot
On every push / PR to `main`: pytest runs with Python 3.12, and
Tailwind CSS is rebuilt. Dependabot opens weekly PRs for pip, npm,
and GitHub Actions updates.

### 13. Full 15-item CRediT taxonomy
The "Contribution type" select on the task form now offers the complete
NISO CRediT 1.0 taxonomy: Conceptualization, Data curation, Formal
analysis, Funding acquisition, Investigation, Methodology, Project
administration, Resources, Software, Supervision, Validation,
Visualization, Writing – original draft, Writing – review & editing,
Other. Existing rows keep their v1 values verbatim (no data migration).

### 14. ICS calendar export for milestones
`/projects/<id>/milestones.ics` returns an iCalendar feed of every
milestone with a date. Researchers subscribe from Google/Apple/Outlook
and get deadlines in their calendar. A "Subscribe to calendar" button
lives on the milestone list page. Hand-rolled RFC 5545 builder — no
new dependency.

### 15. Bulk CSV task import
PMs can upload a CSV at `/projects/<id>/tasks/import/` with columns:
`name` (required), `description`, `deadline` (YYYY-MM-DD),
`contribution_type`, `column` (kanban column name; auto-created if
missing), `assignee_emails` (comma-separated existing member emails).
Unknown assignee emails produce per-row warnings rather than failure;
the whole import runs in one transaction. Each imported task is
audit-logged. "Import CSV" button added to the kanban board toolbar
for PMs.

### 16. Per-project mailing list + archive
PMs can compose an announcement at `/projects/<id>/mailing-list/compose/`
that emails every project member (excluding the sender). Each send is
archived at `/projects/<id>/mailing-list/` (paginated) and each message
has a permalink at `/projects/<id>/mailing-list/<msg_id>/` that future
members can read. Recipients can hit reply — the `Reply-To` header is
the sender's email. Audit-logged as `MAILING_LIST_MESSAGE_SENT`.

### 17. Discord webhook on task approval
Each project has an optional `discord_webhook_url` field in the project
settings (PM edits it on the project edit form). When a task completion
is approved, an embed is POSTed to the Discord channel:
`{title: "Task approved: <name>", project, type, approved_by}` in
navy. SSRF-safe: scheme must be https, host must be `discord.com` or
`discordapp.com`. Failures are swallowed so Discord downtime can never
block task approval. Webhook URL is never leaked to the API, CSV export,
or audit-log metadata.

### 18. GitHub issue / PR linking on tasks
Task form now has a `github_url` field. When set to a github.com issue
or pull-request URL, the task detail sidebar shows a clickable
`owner/repo#N` link with the GitHub octocat icon and an "issue" /
"pull request" label. Kanban cards also get a small GitHub icon when a
link is set. Pattern-based (regex); no API calls, no rate-limit risk.

---

## Golden-path test checklist

1. **Landing + signup + login**
   - Visit `/` as a logged-out user — landing page renders.
   - Sign up at `/accounts/signup/` — you're auto-logged in; welcome email in console.
   - Log out via user menu; log back in.
   - After login, the "My Projects" page now shows **My active tasks** and
     **Pending your approval** widgets when you have anything in flight.
2. **Project + tasks**
   - Create a project — set "Choose contribution tracking" to CRediT.
   - Add a kanban column, then create a task; note the "Task type" select,
     offering CRediT's 14 roles (it appears only on a CRediT project).
   - Assign yourself, mark your own part done, then close the task as lead.
3. **Contribution ledger + downloads**
   - Open the project's **Contribution Tracking** tab — the matrix shows 1 in
     the column matching the task's type, and the list names the task below.
   - Click "Edit" on that row to correct its role, or "Add contribution" to
     credit someone the ledger missed.
   - Reopen the task from its detail view and reload — the earned credit is
     gone; close it again and it is back.
   - Download "Matrix (CSV)" and "List (CSV)" from the same page.
   - Open **Contributions** in the sidebar — the same credit from your side,
     filterable by project, with both downloads carrying the filter.
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

### v2 / v3 work still deferred
- XLSX / ZIP export (CSV is shipped; XLSX adds `openpyxl` dep for little gain).
- Auto-formatted prose CRediT author contribution statement generator.
- Cross-project merit profile / public contributor directory.
- Trust / reliability scoring (v3 — requires careful anti-gaming design).
- Slack webhook (Discord is shipped; Slack is the same pattern).
- OSF / LimeSurvey integrations.
- Bidirectional GitHub sync (currently links are display-only, no API calls).

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
