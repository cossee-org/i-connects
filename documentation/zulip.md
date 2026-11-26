# Connecting a project to Zulip

A project can post its activity to one topic of a Zulip channel, so the
team hears about new tasks, deadlines and each other's progress where it
already talks. Each project has its own connection; a Lead sets it up.

## What gets posted

| Group | Messages |
|---|---|
| Tasks | A task is created (with assignees and deadline), someone is added to a task, an assignee marks their part done, a part is reviewed, the task is completed or approved |
| Deadlines | Once a day: open tasks due within 3 days, and tasks that went overdue since the day before |
| People | Someone joins the project |
| Comments | A new comment on a task, quoted |
| Wiki | A page is created, edited or restored (never pages visible to Leads only) |

Each group is a checkbox on the connection, and **Post to Zulip** pauses
all of them. Tasks created by a CSV import are not announced one by one.

Communications is for the project's Leads only for now. Until the project
is connected, the Communications page is the connection form. Once it is
connected, the page is the **Team chat**: a link that opens the topic and
a box to write to the team from i-CONNECTS. The connection itself then
opens from the page's **Zulip connection** button.

Communications has its own project tab only when **Settings →
Communications → Show the Communications tab** is on (it is off for a new
project). Off, Leads open it from that same Settings card.

## Setting it up

In Zulip:

1. **Settings → Personal → Bots → Add a new bot**. Type: *Incoming webhook*.
   Name it something like `i-CONNECTS`.
2. If the channel is private, subscribe the bot to it.
3. Copy the bot's **email** and **API key**.

In i-CONNECTS, as a Lead of the project:

1. **Settings → Communications → Connect to Zulip** (or the Communications tab, if it is shown).
2. Fill in the organisation URL (`https://your-group.zulipchat.com`), the
   bot's email and API key, the channel and the topic.
3. **Connect**. A test message is sent to the topic; if Zulip refuses it,
   nothing is saved and the reason is shown.

One bot can serve several projects — point each project at its own topic.

## For whoever deploys

- `SITE_URL` must be set, or messages carry names without links.
- `python manage.py migrate` creates the connection table
  (`integrations_zulipconnection`) and the project's
  `show_communications_tab` setting.
- `python manage.py setup_cron` installs the daily deadline reminder
  (08:00, `cron.tasks.zulip_deadline_reminder_cron`).
- Messages are sent by the Celery worker after the change commits, so a
  slow or unreachable Zulip never delays or fails a request.
- Only hosts under `ZULIP_ALLOWED_HOSTS` (default `zulipchat.com`) are
  contacted. Add a self-hosted server's host there to allow it.
- The bot's API key is stored in the database as entered, like the Discord
  webhook URL. It is never rendered back into a page, and only Leads see
  the bot's email.
