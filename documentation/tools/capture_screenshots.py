"""Drive a running i-CONNECTS instance through the user-guide scenario and
photograph every step.

Everything happens through the real web UI in a real (headless) Chromium:
four people sign up, a lead creates a project, invites the others, sets tasks,
members mark their parts done, a reviewer signs off, the lead completes tasks,
and a member writes a wiki page. Screenshots land in ../screenshots/.

Run it against an EMPTY database (see README.md in this folder): the scenario
assumes the project gets id 1 and the tasks ids 1-5.

Environment:
  ICONNECTS_URL   base URL of the running site (default http://localhost:8000)
  DOCS_MAIL_DIR   folder the site writes emails to (docs_settings.py)
  CHROMIUM        path to a Chromium binary (optional)
  NPM_CDN_DIR     a node_modules folder holding easymde and font-awesome@4.7.0,
                  served in place of jsDelivr/BootstrapCDN when those are not
                  reachable (optional)
  HTTPS_CA_BUNDLE CA bundle for fetching Google Fonts through a proxy (optional)
"""

import email
import glob
import os
import re
import ssl
import sys
import urllib.request
from email import policy

from playwright.sync_api import sync_playwright

BASE = os.environ.get("ICONNECTS_URL", "http://localhost:8000")
MAIL = os.environ.get("DOCS_MAIL_DIR", "/tmp/iconnects-docs-mail")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "screenshots")
NPM = os.environ.get("NPM_CDN_DIR", "")
CHROMIUM = os.environ.get("CHROMIUM") or None
CA = os.environ.get("HTTPS_CA_BUNDLE")
PW = "Seagrass#2026"
W, H = 1280, 860

PEOPLE = [
    ("Maya", "Chen", "maya.chen@example.org"),
    ("Liam", "Okafor", "liam.okafor@example.org"),
    ("Sofia", "Ramirez", "sofia.ramirez@example.org"),
    ("Kenji", "Watanabe", "kenji.watanabe@example.org"),
]
MAYA, LIAM, SOFIA, KENJI = (p[2] for p in PEOPLE)

PROJECT_NAME = "Coastal Seagrass Microbiome Survey"
PROJECT_DESC = (
    "A multi-site survey of the microbial communities living on eelgrass "
    "(Zostera marina) leaves along the Pacific coast. We sample six meadows each "
    "season, sequence 16S rRNA, and relate community composition to water "
    "temperature and nutrient load."
)

TASKS = [
    dict(name="Collect eelgrass leaf samples at the Tofino meadows",
         desc="Collect 30 leaf blades per meadow at the three Tofino sites (low tide, "
              "12-14 Oct). Store each blade in sterile RNAlater tubes and log GPS and "
              "water temperature on the field sheet.",
         type="Investigation", assignees=["Liam", "Kenji"], reviewers=["Sofia"],
         start="2026-10-12T08:00", due="2026-10-16T17:00", priority=True),
    dict(name="Write the DNA extraction protocol",
         desc="Adapt the PowerSoil kit protocol for epiphyte biofilms and share it as a wiki page.",
         type="Methodology", assignees=["Sofia"], reviewers=[],
         start="2026-10-06T09:00", due="2026-10-09T17:00"),
    dict(name="Quality-filter the 16S sequencing reads",
         desc="Run DADA2 on the first sequencing batch; remove chimeras and singletons.",
         type="Data curation", assignees=["Kenji"], reviewers=["Liam"],
         start="2026-10-13T09:00", due="2026-10-23T17:00"),
    dict(name="Draft the introduction of the manuscript",
         desc="Two pages on seagrass decline and the role of leaf microbiomes.",
         type="Writing – original draft", assignees=["Maya", "Liam"], reviewers=[],
         start="2026-10-19T09:00", due="2026-11-06T17:00"),
    dict(name="Fit temperature models to community composition",
         desc="PERMANOVA and GLLVMs of community composition against temperature and nutrients.",
         type="Formal analysis", assignees=["Liam"], reviewers=["Maya"],
         start="2026-10-26T09:00", due="2026-11-20T17:00"),
]

PROTOCOL = """Use this protocol for every eelgrass leaf sample collected in the survey.

## Materials

- DNeasy PowerSoil Pro kit
- Sterile scalpel and forceps
- Bead-beater set to 6 m/s

## Steps

1. Cut a 2 cm section from the middle of each leaf blade.
2. Swab the epiphyte layer into the bead tube.
3. Bead-beat for 2 × 45 s, resting on ice in between.
4. Follow the kit protocol from step 5 onwards.

> **Tip:** run one extraction blank per batch of 23 samples.

Samples come from the [[Sampling sites]] listed on their own page.
"""

SITES = """The six meadows sampled each season.

| Site | Region | Depth (m) |
|---|---|---|
| Grice Bay | Tofino | 1.5 |
| Lemmens Inlet | Tofino | 2.0 |
| Browning Passage | Tofino | 1.0 |
| Roberts Bank | Fraser delta | 1.2 |
| Boundary Bay | Fraser delta | 0.8 |
| Esquimalt Lagoon | Victoria | 1.0 |

See the [[DNA extraction protocol]] for how the samples are processed.
"""


# --------------------------------------------------------------------------
# Browser helpers
# --------------------------------------------------------------------------

_SSL = ssl.create_default_context(cafile=CA) if CA else ssl.create_default_context()
_CACHE = {}
_TYPES = {".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2",
          ".woff": "font/woff", ".ttf": "font/ttf", ".svg": "image/svg+xml",
          ".eot": "application/vnd.ms-fontobject"}


def _local(route, path):
    path = path.split("?")[0].split("#")[0]
    route.fulfill(status=200, body=open(path, "rb").read(), headers={
        "Content-Type": _TYPES.get(os.path.splitext(path)[1], "application/octet-stream"),
        "Access-Control-Allow-Origin": "*"})


def _cdn(route):
    """Serve the pages' third-party assets.

    With NPM_CDN_DIR set, jsDelivr / BootstrapCDN files come from the same npm
    packages on disk; everything else (Google Fonts) is fetched with Python,
    which honours HTTPS_PROXY and HTTPS_CA_BUNDLE where Chromium may not.
    """
    url = route.request.url
    if NPM:
        m = re.match(r"https://cdn\.jsdelivr\.net/npm/([^/@]+)(?:@[^/]+)?/(.*)", url)
        if m:
            return _local(route, os.path.join(NPM, m.group(1), m.group(2)))
        m = re.match(r"https://maxcdn\.bootstrapcdn\.com/font-awesome/[^/]+/(.*)", url)
        if m:
            return _local(route, os.path.join(NPM, "font-awesome", m.group(1)))
    if url not in _CACHE:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": route.request.headers.get("user-agent", "Mozilla/5.0")})
            with urllib.request.urlopen(req, context=_SSL, timeout=30) as r:
                _CACHE[url] = (r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read())
        except Exception:  # noqa: BLE001 — a missing font is not worth failing the run
            return route.abort()
    status, ctype, body = _CACHE[url]
    route.fulfill(status=status, body=body,
                  headers={"Content-Type": ctype, "Access-Control-Allow-Origin": "*"})


def new_page(browser):
    ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2)
    for host in ("cdn.jsdelivr.net", "maxcdn.bootstrapcdn.com", "fonts.googleapis.com", "fonts.gstatic.com"):
        ctx.route(f"https://{host}/**", _cdn)
    return ctx.new_page()


def go(pg, path):
    pg.goto(BASE + path)
    pg.wait_for_load_state("networkidle")


def settle(pg, ms=500):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(ms)


HL = "outline: 3px solid #f03d30 !important; outline-offset: 3px !important; border-radius: 6px;"
# For table cells, whose outline would be clipped by the table's edge.
HL_INSET = "outline: 3px solid #f03d30 !important; outline-offset: -3px !important; border-radius: 6px;"


def shot(pg, name, full=False, hl=()):
    """Screenshot the viewport (or the whole page) with ``hl`` outlined in red.

    An item of ``hl`` is a locator, or a (locator, css) pair to outline it
    some other way.

    A whole-page shot grows the viewport to the page's height rather than
    using Playwright's stitched full_page capture, which would paint the
    sticky top bar halfway down the image.
    """
    pg.wait_for_timeout(350)
    for item in hl:
        loc, css = item if isinstance(item, tuple) else (item, HL)
        loc.evaluate("(el, css) => { el.dataset.oldStyle = el.getAttribute('style') || '';"
                     " el.setAttribute('style', el.dataset.oldStyle + ';' + css); }", css)
    if full:
        height = pg.evaluate("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)")
        pg.set_viewport_size({"width": W, "height": max(H, height)})
        pg.wait_for_timeout(300)
    pg.screenshot(path=os.path.join(SHOTS, name + ".png"))
    if full:
        pg.set_viewport_size({"width": W, "height": H})
    pg.evaluate("document.querySelectorAll('[data-old-style]').forEach(el => {"
                " el.setAttribute('style', el.dataset.oldStyle); delete el.dataset.oldStyle; })")
    print("  shot", name)


def shot_email(pg, name, html, height=470):
    """Render an email's HTML body the way a mail client would show it.

    Uses a page of its own: narrowing the app's page would make the site
    collapse its sidebar, a choice it remembers for the rest of the run.
    """
    mail = pg.context.browser.new_page(viewport={"width": 900, "height": height}, device_scale_factor=2)
    mail.set_content("<div style='font-family: Arial, sans-serif; padding: 12px 20px'>" + html + "</div>")
    mail.wait_for_timeout(300)
    mail.screenshot(path=os.path.join(SHOTS, name + ".png"))
    mail.close()
    print("  shot", name)


def login(pg, addr):
    pg.context.clear_cookies()
    go(pg, "/accounts/login/")
    pg.fill("input[name=email]", addr)
    pg.fill("input[name=password]", PW)
    pg.click("button[type=submit]:has-text('Sign in')")
    settle(pg, 200)


def latest_mail(to):
    """(subject, text, html) of the newest email sent to ``to``."""
    for f in sorted(glob.glob(os.path.join(MAIL, "*.log")), key=os.path.getmtime, reverse=True):
        for chunk in reversed(open(f, "rb").read().split(b"\n" + b"-" * 79 + b"\n")):
            if not chunk.strip():
                continue
            m = email.message_from_bytes(chunk.strip(), policy=policy.default)
            if to.lower() in (m["To"] or "").lower():
                text, html = m.get_body(("plain",)), m.get_body(("html",))
                return (m["Subject"], text.get_content() if text else "",
                        html.get_content() if html else "")
    raise LookupError(f"no email to {to} in {MAIL}")


def task_url(n):
    return f"/projects/1/tasks/{n}/"


# The task's Completion table: one row per assignee, with round Done and
# Reviewed tick boxes. Screenshots outline the table cell holding the box.
OWN_DONE = "input[aria-label='Mark your part done']"
OWN_UNDO = "input[aria-label='Mark your part not done']"
TO_REVIEW = "input.completion-tick-review[hx-vals*='\"reviewed\": \"1\"']"


def cell(pg, tick):
    """The table cell holding ``tick``, for ``shot``'s ``hl``."""
    return pg.locator(f"td:has({tick})").first, HL_INSET


def show_completion(pg):
    """Scroll the whole Completion card into view, keeping the task's title
    above it in view too."""
    pg.locator("table[data-assignee-rows]").first.evaluate(
        "el => { el.scrollIntoView({block: 'end'}); window.scrollBy(0, 110); }")


def mark_own(pg, n, comment=None, shots=None):
    go(pg, task_url(n))
    if comment:
        pg.fill("textarea[placeholder='Add a comment']", comment)
        pg.click("button:has-text('Comment')")
        settle(pg)
    if shots:
        show_completion(pg)
        shot(pg, shots[0], hl=[cell(pg, OWN_DONE)])
    pg.locator(OWN_DONE).first.click()
    settle(pg)
    if shots:
        show_completion(pg)
        shot(pg, shots[1], hl=[cell(pg, OWN_UNDO)])


def review_next(pg):
    pg.locator(TO_REVIEW).first.click()
    settle(pg)


def drag_card(pg, task_id, column_index):
    """Drag a Kanban card into another column with real mouse movements."""
    card = pg.locator(f".kanban-column-list [data-task-id='{task_id}']").first
    target = pg.locator(".kanban-column-list").nth(column_index)
    cb, tb = card.bounding_box(), target.bounding_box()
    x0, y0 = cb["x"] + cb["width"] / 2, cb["y"] + 20
    x1, y1 = tb["x"] + tb["width"] / 2, tb["y"] + 60
    pg.mouse.move(x0, y0)
    pg.mouse.down()
    for i in range(1, 21):
        pg.mouse.move(x0 + (x1 - x0) * i / 20, y0 + (y1 - y0) * i / 20)
        pg.wait_for_timeout(30)
    pg.mouse.up()
    pg.wait_for_timeout(800)


def pick_members(pg, field, names, shot_name=None):
    det = pg.locator(f"details:has(input[name={field}])")
    det.locator("summary").click()
    pg.wait_for_timeout(200)
    for n in names:
        det.locator(f"label:has-text('{n}') input[type=checkbox]").check()
    if shot_name:
        shot(pg, shot_name)
    det.locator("summary").click()
    pg.wait_for_timeout(150)


# --------------------------------------------------------------------------
# The scenario, one user-guide section at a time
# --------------------------------------------------------------------------

def s01_signup(pg):
    print("1. Sign-up")
    for i, (given, family, addr) in enumerate(PEOPLE):
        doc = i == 0
        pg.context.clear_cookies()
        go(pg, "/")
        if doc:
            shot(pg, "01_landing", hl=[pg.locator("header a:has-text('Sign up')").first])
        go(pg, "/accounts/signup/")
        pg.fill("input[name=given_name]", given)
        pg.fill("input[name=family_name]", family)
        for name, value in (("email", addr), ("email_confirm", addr),
                            ("password", PW), ("password_confirm", PW)):
            pg.fill(f"input[name={name}]", value)
        pg.check("input[name=agree_terms]")
        if doc:
            shot(pg, "01_signup_filled", hl=[pg.locator("button:has-text('Create account')")])
        pg.click("button:has-text('Create account')")
        settle(pg, 200)
        if doc:
            shot(pg, "01_signup_sent")
        _, text, html = latest_mail(addr)
        link = re.search(r"https?://\S+/accounts/confirm/\S+", text).group(0)
        if doc:
            shot_email(pg, "01_signup_email", html, height=420)
        pg.goto(link)
        settle(pg, 200)
        if doc:
            shot(pg, "01_signup_confirmed")


def s02_signin(pg):
    print("2. Sign-in")
    pg.context.clear_cookies()
    go(pg, "/accounts/login/")
    pg.fill("input[name=email]", MAYA)
    pg.fill("input[name=password]", PW)
    shot(pg, "02_signin_filled", hl=[pg.locator("button[type=submit]:has-text('Sign in')")])
    pg.click("button[type=submit]:has-text('Sign in')")
    settle(pg, 200)
    shot(pg, "02_signin_dashboard")


def s03_project(pg):
    print("3. Create a new project")
    login(pg, MAYA)
    go(pg, "/projects/mine/")
    shot(pg, "03_projects_empty", hl=[pg.locator("main a:has-text('New project')").first])
    pg.locator("main a:has-text('New project')").first.click()
    settle(pg, 200)
    pg.fill("input[name=name]", PROJECT_NAME)
    pg.fill("textarea[name=description]", PROJECT_DESC)
    status = pg.locator("div[x-data]:has(p:text('Choose status'))").first
    status.locator("button").first.click()
    pg.wait_for_timeout(200)
    status.get_by_role("button", name="Active", exact=True).click()
    tracking = pg.locator("div[x-data]:has(p:text('Choose contribution tracking'))").first
    tracking.locator("button").first.click()
    pg.wait_for_timeout(200)
    credit = tracking.locator("button:has-text('Contributor Role Taxonomy')")
    shot(pg, "03_project_credit_menu", hl=[credit])
    credit.click()
    pg.wait_for_timeout(200)
    shot(pg, "03_project_filled", full=True, hl=[pg.locator("button:has-text('Create project')")])
    pg.click("button:has-text('Create project')")
    settle(pg)
    shot(pg, "03_project_created")


def s04_invite(pg):
    print("4. Invite members")
    go(pg, "/projects/1/members/")
    shot(pg, "04_members_page", hl=[pg.locator("button:has-text('Invite member')")])
    for i, who in enumerate(("liam", "sofia", "kenji")):
        doc = i == 0
        go(pg, "/projects/1/members/")
        pg.click("button:has-text('Invite member')")
        pg.wait_for_selector("#user-search-input")
        pg.locator("#user-search-input").press_sequentially(who, delay=60)
        pg.wait_for_selector("#user-search-results button")
        pg.wait_for_timeout(300)
        if doc:
            shot(pg, "04_invite_search", hl=[pg.locator("#user-search-results button").first])
        pg.locator("#user-search-results button").first.click()
        if doc:
            shot(pg, "04_invite_selected", hl=[pg.locator("button:has-text('Send invitation')")])
        pg.click("button:has-text('Send invitation')")
        settle(pg)
        if doc:
            shot(pg, "04_invite_sent")
    go(pg, "/projects/1/members/")
    pg.click("a:has-text('Invitations')")
    settle(pg)
    shot(pg, "04_invitations_pending")


def s05_accept(pg):
    print("5. Accept an invitation")
    _, _, html = latest_mail(LIAM)
    shot_email(pg, "05_invite_email", html, height=330)
    login(pg, LIAM)
    review = pg.locator("a[aria-label^='Review invitation']").first
    shot(pg, "05_dashboard_invite", hl=[review])
    review.click()
    settle(pg)
    shot(pg, "05_invite_review", hl=[pg.locator("button:has-text('Accept invitation')")])
    pg.click("button:has-text('Accept invitation')")
    settle(pg)
    shot(pg, "05_invite_accepted")
    for addr in (SOFIA, KENJI):
        login(pg, addr)
        go(pg, "/invites/")
        pg.locator("main a:has-text('Review')").first.click()
        settle(pg, 200)
        pg.click("button:has-text('Accept invitation')")
        settle(pg, 200)
    login(pg, MAYA)
    go(pg, "/projects/1/members/")
    shot(pg, "05_members_after")


def s06_tasks(pg):
    print("6. Create a new task")
    login(pg, MAYA)
    for i, t in enumerate(TASKS):
        doc = i == 0
        go(pg, "/projects/1/board/")
        if doc:
            new = pg.locator("main button:text-is('New task')").first
            shot(pg, "06_board_empty", hl=[new])
            new.click()
            settle(pg, 300)
        else:
            go(pg, "/projects/1/tasks/new/")
        pg.fill("input[name=name]", t["name"])
        pg.fill("textarea[name=description]", t["desc"])
        if not pg.input_value("select[name=container]"):
            pg.select_option("select[name=container]", label="To Do")
        pg.select_option("select[name=contribution_type]", label=t["type"])
        pg.fill("input[name=start_date]", t["start"])
        pg.fill("input[name=deadline]", t["due"])
        if t.get("priority"):
            pg.locator("label:has(input[name=is_priority])").click()
        if doc:
            pg.locator("input[name=name]").scroll_into_view_if_needed()
            shot(pg, "06_task_details")
        pick_members(pg, "assigned_members", t["assignees"], "06_task_assignees" if doc else None)
        if t["reviewers"]:
            pg.check("input[name=requires_review]")
            pg.wait_for_timeout(200)
            pick_members(pg, "reviewers", t["reviewers"], "06_task_reviewers" if doc else None)
        save = pg.locator("main button[type=submit]:has-text('Save')")
        if doc:
            shot(pg, "06_task_filled", full=True, hl=[save])
        save.click()
        settle(pg)
        if doc:
            shot(pg, "06_task_created")
    go(pg, "/projects/1/board/")
    shot(pg, "06_board_with_tasks")


def s07_mark_done(pg):
    print("7. Mark a task done")
    login(pg, LIAM)
    shot(pg, "07_dashboard_tasks", hl=[pg.locator("main a:has-text('Collect eelgrass leaf samples')").first])
    go(pg, task_url(1))
    pg.fill("textarea[placeholder='Add a comment']",
            "Grice Bay and Lemmens Inlet are done: 60 tubes in the -80 °C freezer, rack B3. "
            "Field sheets are scanned to the shared drive.")
    shot(pg, "07_task_comment", hl=[pg.locator("button:has-text('Comment')").last])
    pg.click("button:has-text('Comment')")
    settle(pg)
    mark_own(pg, 1, shots=("07_task_before", "07_task_after"))
    login(pg, KENJI)
    mark_own(pg, 1, comment="Browning Passage done: 30 tubes, same rack.")


def s08_review(pg):
    print("8. Review task submissions")
    login(pg, SOFIA)
    shot(pg, "08_dashboard_to_review", hl=[pg.locator("main a:has-text('Collect eelgrass leaf samples')").first])
    go(pg, task_url(1))
    show_completion(pg)
    shot(pg, "08_review_before", hl=[cell(pg, TO_REVIEW)])
    review_next(pg)
    show_completion(pg)
    shot(pg, "08_review_partial", hl=[cell(pg, TO_REVIEW)])
    review_next(pg)
    show_completion(pg)
    shot(pg, "08_review_complete")


def s09_complete(pg):
    print("9. Complete a task")
    # Background: the other assignees finish their parts.
    login(pg, SOFIA)
    mark_own(pg, 2, comment="The protocol is on the wiki: 'DNA extraction protocol'.")
    login(pg, KENJI)
    mark_own(pg, 3)
    login(pg, LIAM)
    go(pg, task_url(3))
    review_next(pg)
    mark_own(pg, 4)
    login(pg, MAYA)
    mark_own(pg, 4)
    # The lead closes a task with no review step.
    go(pg, task_url(2))
    btn = pg.locator("button:has-text('Mark Completed')").first
    shot(pg, "09_complete_before", hl=[btn])
    btn.click()
    settle(pg)
    shot(pg, "09_complete_after", hl=[pg.locator("button:has-text('Mark Not Complete')").first])
    go(pg, task_url(4))
    pg.locator("button:has-text('Mark Completed')").first.click()
    settle(pg)
    # Tidy the board: finished cards to Done, the analysis to In Progress.
    go(pg, "/projects/1/board/")
    pg.wait_for_timeout(500)
    for task_id in (1, 2, 3, 4):
        drag_card(pg, task_id, 2)
        go(pg, "/projects/1/board/")
        pg.wait_for_timeout(400)
    drag_card(pg, 5, 1)
    go(pg, "/projects/1/board/")
    shot(pg, "09_board_after")


def s10_contributions(pg):
    print("10. Track contributions")
    login(pg, MAYA)
    go(pg, "/projects/1/contribution-tracking/")
    shot(pg, "10_by_member")
    # Sort by a role (one click: most first) — Sofia, the protocol's author,
    # moves to the top for Methodology.
    sort = "#contributions-by-member-container th button:has-text('Methodology')"
    pg.locator(sort).first.click()
    settle(pg)
    shot(pg, "10_by_member_sorted", hl=[(pg.locator(sort).first, HL_INSET)])
    pg.click("text=Every contribution")
    settle(pg)
    add = pg.locator("main :text-is('Add contribution')").first
    shot(pg, "10_every_contribution", hl=[add])
    # A reviewer's sign-off earns no automatic credit; the lead records it.
    add.click()
    pg.wait_for_selector("#contribution-modal select[name=task]")
    pg.wait_for_timeout(300)
    pg.select_option("select[name=task]", label=TASKS[0]["name"])
    pg.select_option("select[name=user]", label="Sofia Ramirez")
    pg.select_option("select[name=contribution_type]", label="Validation")
    submit = pg.locator("#contribution-modal button[type=submit]").first
    shot(pg, "10_add_contribution", hl=[submit])
    submit.click()
    settle(pg)
    go(pg, "/projects/1/contribution-tracking/")
    pg.click("text=Every contribution")
    settle(pg)
    shot(pg, "10_every_contribution_after", hl=[pg.locator("main tr:has-text('Validation')").first])
    shot(pg, "10_csv_buttons", hl=[pg.locator("main a:has-text('Matrix (CSV)')").first,
                                   pg.locator("main a:has-text('List (CSV)')").first])
    with pg.expect_download() as dl:
        pg.locator("main a:has-text('Matrix (CSV)')").first.click()
    assert dl.value.failure() is None, "the matrix download failed"
    login(pg, LIAM)
    go(pg, "/contributions/")
    shot(pg, "10_my_contributions", full=True)


def s11_wiki(pg):
    print("11. Write a project wiki")
    login(pg, SOFIA)
    go(pg, "/projects/1/wiki/")
    new = pg.locator("main a:has-text('Write the first page')").first
    shot(pg, "11_wiki_empty", hl=[new])
    new.click()
    settle(pg, 800)
    pg.fill("input[name=title]", "DNA extraction protocol")
    pg.evaluate("t => document.querySelector('.CodeMirror').CodeMirror.setValue(t)", PROTOCOL)
    pg.fill("input[name=summary]", "First version of the extraction protocol")
    save = pg.locator("main button[type=submit]").first
    shot(pg, "11_wiki_editor", full=True, hl=[save])
    save.click()
    settle(pg)
    shot(pg, "11_wiki_page", full=True, hl=[pg.locator("main a:has-text('+ Sampling sites')").first])
    # Follow the red link to create the page it points to.
    pg.locator("main a:has-text('+ Sampling sites')").first.click()
    settle(pg, 800)
    pg.evaluate("t => document.querySelector('.CodeMirror').CodeMirror.setValue(t)", SITES)
    pg.fill("input[name=summary]", "List of the six survey meadows")
    pg.locator("main button[type=submit]").first.click()
    settle(pg)
    shot(pg, "11_wiki_linked_page", full=True)
    # A second person edits the protocol: a new revision in the history.
    login(pg, LIAM)
    go(pg, "/projects/1/wiki/")
    pg.locator("main a:has-text('DNA extraction protocol')").first.click()
    settle(pg)
    pg.locator("main a:has-text('Edit')").first.click()
    settle(pg, 800)
    pg.evaluate("""() => { const cm = document.querySelector('.CodeMirror').CodeMirror;
        cm.setValue(cm.getValue().replace('- Bead-beater set to 6 m/s',
          '- Bead-beater set to 6 m/s\\n- Qubit fluorometer for DNA yield')); }""")
    pg.fill("input[name=summary]", "Added the Qubit to the materials")
    pg.locator("main button[type=submit]").first.click()
    settle(pg)
    pg.locator("main a:has-text('History')").first.click()
    settle(pg)
    shot(pg, "11_wiki_history")
    go(pg, "/projects/1/wiki/")
    shot(pg, "11_wiki_list")


def shrink_pngs():
    """Re-save every screenshot with a 256-colour palette (about a third of the
    size; UI screenshots have few colours, so text stays sharp)."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed; screenshots left uncompressed")
        return
    for f in glob.glob(os.path.join(SHOTS, "*.png")):
        im = Image.open(f).convert("RGB")
        im.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                    dither=Image.Dither.NONE).save(f, optimize=True)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    only = set(sys.argv[1:])
    steps = [s01_signup, s02_signin, s03_project, s04_invite, s05_accept, s06_tasks,
             s07_mark_done, s08_review, s09_complete, s10_contributions, s11_wiki]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        pg = new_page(browser)
        for step in steps:
            if not only or step.__name__[:3] in only:
                step(pg)
        browser.close()
    shrink_pngs()


if __name__ == "__main__":
    main()
