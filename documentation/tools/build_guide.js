// Builds documentation/i-CONNECTS_User_Guide.docx from the screenshots in
// documentation/screenshots/.
//
//   node documentation/tools/build_guide.js
//
// The .docx it writes is the editable source of the guide: open it in Word or
// LibreOffice, edit, and export it to PDF. Re-running this script overwrites
// the .docx, so once the guide has been edited by hand, edit the .docx rather
// than this file (or carry the edits back into the text below first).

const fs = require("fs");
const path = require("path");
const {
  AlignmentType, BorderStyle, Document, Footer, Header, HeadingLevel, ImageRun,
  LevelFormat, Packer, PageBreak, PageNumber, Paragraph, ShadingType, Table,
  TableCell, TableOfContents, TableRow, TextRun, WidthType,
} = require("docx");

const ROOT = path.join(__dirname, "..");
const SHOTS = path.join(ROOT, "screenshots");
const OUT = path.join(ROOT, "i-CONNECTS_User_Guide.docx");

const NAVY = "323895";
const CORAL = "F03D30";
const GREY = "5F6577";
const LAVENDER = "EBEEF7";
const FONT = "Arial";

// US Letter, 1" margins: 6.5" of text width.
const PAGE_W = 12240, PAGE_H = 15840, MARGIN = 1440;
const TEXT_W = PAGE_W - 2 * MARGIN; // 9360 DXA
const MAX_IMG_W = 552; // px at 96 dpi = 5.75"
const MAX_IMG_H = 600; // keep a figure and its caption on one page

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}

// Inline markup: **bold**, `ui label` (bold navy, for things on screen).
function runs(text, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, ...base }));
    else out.push(new TextRun({ text: t.slice(1, -1), bold: true, color: NAVY, ...base }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out;
}

const p = (text, opts = {}) =>
  new Paragraph({ children: runs(text), spacing: { after: 120, line: 276 }, ...opts });

// Numbered steps run on across figures and notes until the next heading,
// which starts a fresh list.
let listCount = 0;
let listRef = null;
function newList() {
  listRef = `steps${++listCount}`;
  numberingConfigs.push({
    reference: listRef,
    levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 360, hanging: 360 } } } }],
  });
}
function steps(items) {
  if (!listRef) newList();
  return items.map((t) => new Paragraph({ numbering: { reference: listRef, level: 0 }, children: runs(t), spacing: { after: 80, line: 276 } }));
}

function h1(text) {
  newList();
  return new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: true, children: [new TextRun(text)] });
}
function h2(text) {
  newList();
  return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
}

const bullets = (items) =>
  items.map((t) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs(t), spacing: { after: 80, line: 276 } }));

let figureCount = 0;
function figure(name, caption) {
  const file = path.join(SHOTS, name + ".png");
  const { w, h } = pngSize(file);
  let width = MAX_IMG_W, height = Math.round((h * MAX_IMG_W) / w);
  if (height > MAX_IMG_H) { height = MAX_IMG_H; width = Math.round((w * MAX_IMG_H) / h); }
  figureCount += 1;
  return [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      keepNext: true,
      spacing: { before: 160, after: 60 },
      children: [new ImageRun({
        type: "png", data: fs.readFileSync(file),
        transformation: { width, height },
        altText: { title: `Figure ${figureCount}`, description: caption, name },
      })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 240 },
      children: [
        new TextRun({ text: `Figure ${figureCount}. `, bold: true, size: 18, color: GREY }),
        ...runs(caption, { size: 18, color: GREY, italics: true }),
      ],
    }),
  ];
}

// A shaded call-out box: a one-cell table with a coloured left border.
function note(label, text, color = NAVY) {
  const border = { style: BorderStyle.SINGLE, size: 4, color: "D5DAEA" };
  return [
    new Table({
      width: { size: TEXT_W, type: WidthType.DXA },
      columnWidths: [TEXT_W],
      rows: [new TableRow({ cantSplit: true, children: [new TableCell({
        width: { size: TEXT_W, type: WidthType.DXA },
        shading: { fill: LAVENDER, type: ShadingType.CLEAR, color: "auto" },
        margins: { top: 100, bottom: 100, left: 180, right: 180 },
        borders: { top: border, bottom: border, right: border, left: { style: BorderStyle.SINGLE, size: 24, color } },
        children: [new Paragraph({ spacing: { line: 276 }, children: [new TextRun({ text: label + "  ", bold: true, color }), ...runs(text)] })],
      })] })],
    }),
    new Paragraph({ spacing: { after: 120 }, children: [] }),
  ];
}
const tip = (t) => note("Tip", t, NAVY);
const important = (t) => note("Important", t, CORAL);

function table(headers, rows, widths) {
  const border = { style: BorderStyle.SINGLE, size: 4, color: "C9CFE3" };
  const borders = { top: border, bottom: border, left: border, right: border };
  const cell = (text, i, head) => new TableCell({
    width: { size: widths[i], type: WidthType.DXA },
    borders,
    shading: head ? { fill: NAVY, type: ShadingType.CLEAR, color: "auto" } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [new Paragraph({ children: head ? [new TextRun({ text, bold: true, color: "FFFFFF" })] : runs(text) })],
  });
  return [
    new Table({
      width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
      columnWidths: widths,
      rows: [
        new TableRow({ tableHeader: true, children: headers.map((t, i) => cell(t, i, true)) }),
        ...rows.map((r) => new TableRow({ cantSplit: true, children: r.map((t, i) => cell(t, i, false)) })),
      ],
    }),
    new Paragraph({ spacing: { after: 160 }, children: [] }),
  ];
}

const numberingConfigs = [
  { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: 360, hanging: 360 } } } }] },
];

// ---------------------------------------------------------------------------
// Content
// ---------------------------------------------------------------------------

const titlePage = [
  new Paragraph({ spacing: { before: 2400 }, alignment: AlignmentType.LEFT, children: [
    new TextRun({ text: "i", bold: true, size: 72, color: CORAL }),
    new TextRun({ text: "•CONNECTS", bold: true, size: 72, color: NAVY }),
  ] }),
  new Paragraph({ spacing: { before: 120, after: 120 }, children: [new TextRun({ text: "User Guide", size: 56, color: NAVY })] }),
  new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: CORAL, space: 8 } },
    spacing: { after: 360 },
    children: [new TextRun({ text: "Projects, tasks, reviews, contribution tracking and the project wiki", size: 28, color: GREY })],
  }),
  p("This guide walks through the everyday workflow of i-CONNECTS, the collaboration platform for tracking who does what in distributed research projects: creating an account, setting up a project, inviting collaborators, assigning and reviewing tasks, recording CRediT contributions, and documenting the project in its wiki."),
  p("Every screenshot was taken from a running copy of i-CONNECTS in a web browser, using a fictitious demo project. Names, email addresses and data in the screenshots are made up."),
  new Paragraph({ spacing: { before: 1800 }, children: [new TextRun({ text: "Version 1.0 · October 2026", color: GREY })] }),
  new Paragraph({ children: [new PageBreak()] }),
];

const contents = [
  new Paragraph({ spacing: { after: 240 }, border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: CORAL, space: 6 } },
    children: [new TextRun({ text: "Contents", size: 36, bold: true, color: NAVY })] }),
  new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
  new Paragraph({ spacing: { before: 120 }, children: [new TextRun({ text: "If the table of contents is empty, right-click it and choose Update Field (Word) or Update Index (LibreOffice).", italics: true, size: 18, color: GREY })] }),
];

const about = [
  h1("About this guide"),
  p("The guide follows one small, made-up project from start to finish. Each chapter covers one task and can be read on its own; read in order, they tell the whole story of a task's life — from the moment it is created to the credit it earns."),
  ...tip("In the screenshots, a **red outline** marks the button, link or field to use next. The outline is not part of i-CONNECTS itself."),
  h2("The example project"),
  p("Dr Maya Chen leads the **Coastal Seagrass Microbiome Survey**, which tracks contributions with the CRediT taxonomy. She works with three collaborators:"),
  ...table(["Person", "Role in the project", "What they do in this guide"], [
    ["Maya Chen", "Lead", "Creates the project, invites the team, creates tasks, completes tasks, corrects the contribution record."],
    ["Liam Okafor", "Member", "Accepts the invitation, marks his part of a task done, reviews a colleague's task, edits the wiki."],
    ["Sofia Ramirez", "Member", "Reviews the field-sampling task and writes the project's first wiki pages."],
    ["Kenji Watanabe", "Member", "Works on the field sampling and the sequencing-data clean-up."],
  ], [1900, 1900, 5560]),
  h2("Words used in this guide"),
  ...table(["Term", "Meaning"], [
    ["Lead", "A project member with the Lead privilege. Leads invite people, create tasks, complete tasks and correct contributions. The person who creates a project is its first Lead."],
    ["Member", "Anyone else who belongs to the project. Members work on tasks, review them when asked, comment and write the wiki."],
    ["Assignee", "A member a task is assigned to. Each assignee marks their own part of the task done."],
    ["Reviewer", "A member asked to sign off the assignees' work on a task that requires review."],
    ["CRediT", "The Contributor Role Taxonomy (credit.niso.org): 14 standard roles, such as Investigation or Data curation. A contribution records that a person contributed in one of these roles."],
  ], [1900, 7460]),
];

const ch1 = [
  h1("1. Sign up"),
  p("You need an i-CONNECTS account to create or join projects. Signing up takes a minute: you fill in a short form and confirm your email address."),
  ...steps([
    "Open i-CONNECTS in your browser and click `Sign up` in the top-right corner (or `Create an account` on the home page).",
  ]),
  ...figure("01_landing", "The i-CONNECTS home page. `Sign up` is in the top-right corner."),
  ...steps([
    "Fill in the form: your given name, family name (required), email address twice, and a password twice. Your password must have at least 8 characters, must not be a common password or only numbers, and must not resemble your name or email.",
    "Tick `I agree to the Terms of Use and Privacy Policy`, then click `Create account`.",
  ]),
  ...figure("01_signup_filled", "The sign-up form, filled in."),
  p("i-CONNECTS now asks you to check your inbox. Your account cannot be used until you confirm your email address."),
  ...figure("01_signup_sent", "After you create the account, a confirmation link is sent to your email address."),
  ...steps([
    "Open the email titled **Confirm your i-connects email address** and click `Confirm email` (or copy the link into your browser). The link expires after a few days.",
  ]),
  ...figure("01_signup_email", "The confirmation email."),
  p("The link signs you in and confirms your account. Click `Go to dashboard` to start."),
  ...figure("01_signup_confirmed", "Email confirmed: you are signed in."),
  ...tip("No email? Check your spam folder, then use `Resend confirmation email` on the Check-your-email page or under the sign-in form."),
];

const ch2 = [
  h1("2. Sign in"),
  ...steps([
    "Click `Sign in` in the top-right corner of any public page.",
    "Enter the email address and password you signed up with, then click `Sign in`.",
  ]),
  ...figure("02_signin_filled", "The sign-in page."),
  p("You arrive at your **Dashboard**. Its three counters show how many projects you belong to, how many tasks are waiting on you and how many requests (invitations and join requests) need an answer. Below them, `Active tasks and requests` lists everything that needs your attention; a brand-new account has nothing there yet."),
  ...figure("02_signin_dashboard", "The Dashboard of a new account. The sidebar on the left leads to every part of i-CONNECTS."),
  p("The sidebar is always available once you are signed in:"),
  ...bullets([
    "`Projects` — the projects you belong to, and the button to create a new one.",
    "`Contributions` — the CRediT credit you have earned across all your projects.",
    "`Notifications` — updates about your tasks, reviews and invitations.",
    "`Tasks` — every task assigned to you or waiting for your review.",
    "`Requests` — invitations you have received and join requests to your projects.",
    "`Project discovery` and `People discovery` — browse public projects and researchers on the platform.",
  ]),
  ...tip("Forgot your password? Click `Forgot password?` under the password field and follow the link that is emailed to you. For your security, ten failed sign-in attempts within 15 minutes lock the account; an email with an unlock link is sent to you."),
];

const ch3 = [
  h1("3. Create a new project"),
  p("A project is the shared space for a piece of research: its members, tasks, contribution record and wiki. Whoever creates a project becomes its first **Lead**."),
  ...steps([
    "Click `Projects` in the sidebar, then `New project`. (The `+` button in the top bar does the same from anywhere.)",
  ]),
  ...figure("03_projects_empty", "The Projects page. A new account has no projects yet."),
  ...steps([
    "Enter a `Project name` (up to 200 characters) and a `Description` (up to 2,500 characters) that tells collaborators what the project is about.",
    "Choose the project's `status`: Planning, Active, Paused, Completed or Inactive.",
    "Choose its `visibility`. Public projects can be found by every i-CONNECTS user under Project discovery; private projects are not listed there.",
    "Choose how contributions are tracked. Pick `Contributor Role Taxonomy (CRediT)` to record each member's contributions in CRediT's 14 roles; pick `None` if you do not need contribution tracking.",
  ]),
  ...figure("03_project_credit_menu", "Choosing CRediT contribution tracking."),
  ...important("Choose CRediT when you create the project if you want i-CONNECTS to keep a record of who contributed what. On a project without it, tasks have no task type and completing them records no contributions."),
  ...steps(["Click `Create project`."]),
  ...figure("03_project_filled", "The completed new-project form."),
  p("The new project opens on its overview page. The `Lead` badge under the title shows your privilege in this project. The tabs give you access to everything in the project:"),
  ...bullets([
    "`Project` — the overview, with counts of tasks, overdue tasks, join requests and contributors.",
    "`Tasks` — the task board (Kanban, list and calendar views).",
    "`Contributors` — the members, their privileges, and invitations.",
    "`Contribution Tracking` — the CRediT record of the project.",
    "`Wiki` — the project's shared documentation.",
    "`Settings` (Leads only) — edit the project's details.",
  ]),
  ...figure("03_project_created", "The new project's overview page."),
];

const ch4 = [
  h1("4. Invite members to a project"),
  p("Only a Lead can invite people. You can invite someone who already has an i-CONNECTS account, or anyone else by their email address."),
  ...steps([
    "Open the project and click the `Contributors` tab, then `Invite member`.",
  ]),
  ...figure("04_members_page", "The Contributors tab of a new project, with the Lead as its only member."),
  ...steps([
    "Start typing the person's name or email address. Matching i-CONNECTS users appear below the field; click the right one. If the person is not on i-CONNECTS yet, type their full email address and choose `Invite \"…\" by email` instead.",
  ]),
  ...figure("04_invite_search", "Searching for a collaborator by name."),
  ...steps([
    "Choose the `Privilege` the person will have: `Member` for most collaborators, or `Lead` for someone who should help manage the project.",
    "Click `Send invitation`.",
  ]),
  ...figure("04_invite_selected", "The invitation, ready to send."),
  p("i-CONNECTS emails the invitation and shows it to the person in their Dashboard. A red counter appears on the `Invitations` button."),
  ...figure("04_invite_sent", "Invitation sent."),
  p("Repeat for everyone you want to invite. Click `Invitations` to see the invitations that have not been answered yet; you can `Resend` or `Cancel` each one."),
  ...figure("04_invitations_pending", "Pending invitations, with Resend and Cancel."),
];

const ch5 = [
  h1("5. Accept an invitation"),
  p("When someone invites you to a project, you hear about it in two places: by email, and on your i-CONNECTS Dashboard."),
  ...figure("05_invite_email", "The invitation email. The link opens the invitation in i-CONNECTS."),
  ...steps([
    "Click the link in the email, or sign in and find the invitation under `Active tasks and requests` on your Dashboard (also under `Requests` in the sidebar). Click `Review`.",
  ]),
  ...figure("05_dashboard_invite", "An invitation waiting on the invitee's Dashboard."),
  ...steps([
    "Check the project and who invited you, then click `Accept invitation`. (`Decline` turns it down.)",
  ]),
  ...figure("05_invite_review", "The invitation page."),
  p("The project opens, with the `Member` badge under its title. It now appears under `Projects` in your sidebar."),
  ...figure("05_invite_accepted", "Joined: the invitee is now a member of the project."),
  p("Back on the Contributors tab, the Lead sees everyone who has joined. From here a Lead can change a member's privilege (choose it and click `Save`) or remove a member."),
  ...figure("05_members_after", "The project's members after all three invitations were accepted."),
];

const ch6 = [
  h1("6. Create a new task"),
  p("Tasks are the units of work in a project. Each task has assignees who do the work, optionally reviewers who check it, and — on a CRediT project — a task type: the CRediT role its contributors are credited with when the task is completed. Leads create tasks."),
  ...steps([
    "Open the project's `Tasks` tab and click `+ New task`. (The `+` at the top of a column creates a task in that column.)",
  ]),
  ...figure("06_board_empty", "The Kanban board of a project with no tasks yet."),
  ...steps([
    "Give the task a clear `Task name` and a `Description` of what has to be done.",
    "Pick the `Kanban column` the task starts in, usually To Do.",
    "Pick the `Task type`: the CRediT role this work represents, for example Investigation for field work or Data curation for cleaning data.",
    "Optionally set a `Start date` and `Due date`, and tick `Mark as high priority` for urgent work.",
  ]),
  ...figure("06_task_details", "The first part of the new-task form."),
  ...steps([
    "Open `Assignees` and tick each member who will work on the task. Assignees may include you.",
  ]),
  ...figure("06_task_assignees", "Choosing the task's assignees."),
  ...steps([
    "If the work should be checked before it counts, tick `Require review`, open `Reviewers` and tick at least one reviewer.",
    "Click `Save`.",
  ]),
  ...figure("06_task_reviewers", "Choosing a reviewer for a task that requires review."),
  ...figure("06_task_filled", "The rest of the form, ready to save."),
  p("The task appears on the board. Its card shows the dates, the number of assignees and — with the eye icon — the number of reviewers; a red flag marks high priority. Click a card to open the task."),
  ...figure("06_task_created", "The new task on the Kanban board."),
  p("In this guide the Lead creates four more tasks in the same way. The `List` and `Calendar` buttons above the board show the same tasks as a table or on a calendar."),
  ...figure("06_board_with_tasks", "The board with all five tasks of the example project."),
];

const ch7 = [
  h1("7. Mark a task done (as an assigned member)"),
  p("When you have finished your part of a task, mark it done. On a task with several assignees, each assignee marks their own part; the task itself is completed later, by a reviewer's sign-off or by a Lead."),
  ...steps([
    "Find the task under `Active tasks and requests` on your Dashboard (or under `Tasks` in the sidebar) and click it.",
  ]),
  ...figure("07_dashboard_tasks", "The assignee's Dashboard lists the tasks assigned to them."),
  ...steps([
    "Optionally, leave a comment for your collaborators — for example where the results are — and click `Comment`.",
  ]),
  ...figure("07_task_comment", "Adding a comment to the task."),
  ...steps([
    "Click `Mark my part done`.",
  ]),
  ...figure("07_task_before", "The task page before marking done. The Completion table shows each assignee's progress."),
  p("The `Completion` table now shows a check mark and the date beside your name. On a task that requires review, it reads `Awaiting review` until a reviewer signs your work off."),
  ...figure("07_task_after", "Marked done and awaiting review."),
  ...tip("Marked done by mistake? Click `Undo my mark`. You can undo until the task is completed."),
];

const ch8 = [
  h1("8. Review task submissions (as an assigned reviewer)"),
  p("If a task requires review, each assignee's work has to be signed off by one of the task's reviewers. A submission can be reviewed once its assignee has marked it done."),
  ...steps([
    "Open the task from your Dashboard. Tasks waiting for your review carry a `To review` tag.",
  ]),
  ...figure("08_dashboard_to_review", "The reviewer's Dashboard, with the task to review tagged To review."),
  ...steps([
    "Check the work — the task's comments are a good place for assignees to say where it is.",
    "In the `Reviewed` column, click `Mark reviewed` beside each assignee whose work you accept.",
  ]),
  ...figure("08_review_before", "Both assignees have marked their part done; each submission can now be reviewed."),
  p("Each review is recorded with your name and the date. You can take it back with `Mark not reviewed`."),
  ...figure("08_review_partial", "One submission reviewed, one still to review."),
  p("When the last submission is reviewed, the task completes itself: its status changes to `Complete`, the page names you under `Closed by`, and each assignee is credited with the task's CRediT role."),
  ...figure("08_review_complete", "All submissions reviewed: the task is complete."),
  ...tip("Leads can review submissions on any task that requires review, in the same way as the named reviewers."),
];

const ch9 = [
  h1("9. Complete a task (as a project lead)"),
  p("A Lead decides when a task is finished. This is how tasks without a review step are completed, and a Lead can also complete any other task whenever they judge it done."),
  ...steps([
    "Open the task. Check the `Completion` table: who has marked their part done (and, where review is required, whose work has been reviewed).",
    "Click `Mark Completed`.",
  ]),
  ...figure("09_complete_before", "A finished task without review, ready to be completed by the Lead."),
  p("The task is now `Complete`, with your name under `Closed by`. On a CRediT project, completing a task is what records contributions: each assignee who marked their own part done — and, on a task that requires review, had it reviewed — is credited with the task's type."),
  ...figure("09_complete_after", "The completed task. Its marks are now locked."),
  ...important("Assignees who did not mark their part done receive no credit when the task is completed. Before completing, a Lead can set a mark on someone's behalf with the `Mark done` button in their row of the Completion table."),
  ...tip("Completed a task too early? `Mark Not Complete` reopens it and withdraws the credit it recorded; completing it again records the credit anew. While a task is complete it is locked: it cannot be edited or deleted, and its marks cannot change."),
  p("A task's Kanban column is separate from whether it is complete. To keep the board tidy, drag finished cards to the `Done` column, and work in progress to `In Progress`. A green check on a card shows that the task is complete."),
  ...figure("09_board_after", "The board after the Lead has dragged the cards to their columns."),
];

const ch10 = [
  h1("10. Track the contribution of members"),
  p("On a project that tracks contributions with CRediT, i-CONNECTS keeps a ledger of who contributed what. Each completed task adds one contribution per credited assignee, under the task's type. Every member of the project can see the record in the `Contribution Tracking` tab."),
  h2("Contributions by member"),
  p("The first view is a grid of every member against all 14 CRediT roles, with totals for each person and each role. Click a column heading to sort by it."),
  ...figure("10_by_member", "Contributions by member: all members against all 14 CRediT roles (the table scrolls sideways)."),
  p("Tick `Only roles and members with contributions` to hide the empty columns and rows."),
  ...figure("10_by_member_filtered", "The same grid, narrowed to the roles that have contributions."),
  h2("Every contribution"),
  p("`Every contribution` lists the individual records: who was credited, for which task, in which role, when, and who closed the task."),
  ...figure("10_every_contribution", "Every contribution, one row per record."),
  h2("Correcting the record (Leads)"),
  p("Credit is written automatically, but a Lead can correct it. `Edit` on a row changes the role a contribution is recorded under, or removes it. `Add contribution` credits someone the automatic record passed over — for example a reviewer, whose sign-off does not by itself earn credit, or someone who helped without being an assignee."),
  ...steps([
    "Click `Add contribution`.",
    "Choose the `Task`, the `Member` and the `CRediT role`, then click `Add contribution`.",
  ]),
  ...figure("10_add_contribution", "Crediting the reviewer with Validation on the sampling task."),
  p("Contributions entered by hand are labelled `Added by hand`, and are kept even if the task is later reopened."),
  ...figure("10_every_contribution_after", "The hand-entered contribution in the list."),
  h2("Downloading the record"),
  p("`Matrix (CSV)` downloads the grid and `List (CSV)` the individual records, ready to open in a spreadsheet — for example to write an authorship or CRediT statement. Every member can download the matrix; the list, which includes members' email addresses and ORCID iDs, is available to Leads only."),
  ...figure("10_csv_buttons", "The download buttons."),
  h2("Your own contributions"),
  p("`Contributions` in the sidebar shows your own credit across every project you belong to: a count for each CRediT role and the list of contributions behind them. Filter it by project with the drop-down at the top, and download it with the same CSV buttons."),
  ...figure("10_my_contributions", "A member's own Contributions page."),
];

const ch11 = [
  h1("11. Write a project wiki"),
  p("Each project has a wiki: shared pages for protocols, decisions and onboarding notes. Every member can read it and write pages, and every save is kept, so nothing is lost to an edit."),
  ...steps([
    "Open the project's `Wiki` tab and click `New page` (or `Write the first page` on an empty wiki).",
  ]),
  ...figure("11_wiki_empty", "An empty project wiki."),
  ...steps([
    "Enter a `Page title`.",
    "Write the page in `Page content`. The editor uses Markdown: use the toolbar for bold, italics, headings, lists, links and images, or type the Markdown yourself (for example `## Heading` or `- list item`). The eye button previews the page.",
    "To link to another page of the wiki, write its title in double square brackets, such as [[Sampling sites]]. To show different words, write [[Sampling sites|the sites]].",
    "Optionally describe your edit under `What changed`; it is shown in the page history.",
    "Click `Create page`.",
  ]),
  ...figure("11_wiki_editor", "Writing a wiki page in Markdown."),
  p("The page shows its headings in an `On this page` panel. A link to a page that does not exist yet is shown in red and listed under `Pages this one links to that don't exist yet`: click it to create that page with its title filled in."),
  ...figure("11_wiki_page", "The published page. The red link points to a page that has not been written yet."),
  ...figure("11_wiki_linked_page", "The linked page, created from the red link. Markdown tables are supported."),
  h2("Editing and page history"),
  p("Click `Edit` on any page to change it. Each save creates a new revision. `History` lists every revision with its author, date and summary; open one to see exactly what changed, or restore it to bring its text back as a new revision."),
  ...figure("11_wiki_history", "The history of a page edited by two members."),
  ...tip("Leads can lock a page, with `Lock this page` on the edit form, so that only Leads can change it — useful for agreed protocols."),
  p("The wiki's front page lists all pages, newest edits first or A–Z, and the search box finds words in page titles and text."),
  ...figure("11_wiki_list", "The list of wiki pages, with search."),
];

const reference = [
  h1("Quick reference: who can do what"),
  ...table(["Action", "Lead", "Member"], [
    ["Invite members, change privileges, remove members", "Yes", "No"],
    ["Create, edit and delete tasks", "Yes", "No"],
    ["Mark own part of a task done", "Yes, if assigned", "Yes, if assigned"],
    ["Review submissions on a task that requires review", "Yes", "If named as a reviewer"],
    ["Mark a task Completed / Not Complete", "Yes", "No"],
    ["See contribution tracking and download the matrix", "Yes", "Yes"],
    ["Download the contribution list (emails, ORCID iDs)", "Yes", "No"],
    ["Add, edit or remove contributions", "Yes", "No"],
    ["Read and write wiki pages", "Yes", "Yes"],
    ["Lock wiki pages", "Yes", "No"],
  ], [5160, 2100, 2100]),
  h2("A task's life in brief"),
  ...steps([
    "A **Lead** creates the task, with its type, assignees and (optionally) reviewers.",
    "Each **assignee** does their part and clicks `Mark my part done`.",
    "If the task requires review, a **reviewer** clicks `Mark reviewed` for each submission; the last review completes the task.",
    "Otherwise a **Lead** clicks `Mark Completed`.",
    "Completion credits each assignee who marked their part done (and was reviewed, where required) with the task's CRediT role, visible under `Contribution Tracking`.",
  ]),
];

// ---------------------------------------------------------------------------
// Document
// ---------------------------------------------------------------------------

const doc = new Document({
  creator: "i-CONNECTS",
  title: "i-CONNECTS User Guide",
  description: "How to use i-CONNECTS: sign-up, projects, invitations, tasks, reviews, contribution tracking and the wiki.",
  features: { updateFields: true },
  styles: {
    default: { document: { run: { font: FONT, size: 21, color: "222433" } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, color: NAVY, font: FONT },
        paragraph: { spacing: { before: 0, after: 240 }, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: CORAL, space: 6 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: NAVY, font: FONT },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1, keepNext: true } },
    ],
  },
  numbering: { config: numberingConfigs },
  sections: [
    {
      properties: { page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
      children: titlePage,
    },
    {
      properties: { page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN }, pageNumbers: { start: 1 } } },
      headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: "i-CONNECTS User Guide", size: 16, color: GREY })] })] }) },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: GREY })] })] }) },
      children: [
        ...contents, ...about, ...ch1, ...ch2, ...ch3, ...ch4, ...ch5, ...ch6,
        ...ch7, ...ch8, ...ch9, ...ch10, ...ch11, ...reference,
      ],
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log(`Wrote ${OUT} (${figureCount} figures)`);
});
