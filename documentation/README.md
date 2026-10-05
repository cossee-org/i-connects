# i-CONNECTS user guide

| File | What it is |
|---|---|
| `i-CONNECTS_User_Guide.docx` | **The editable source.** Open it in Word, LibreOffice or Google Docs. |
| `i-CONNECTS_User_Guide.pdf` | The guide exported to PDF, for reading and sharing. |
| `screenshots/` | The 52 screenshots used in the guide, at 2× resolution. |
| `tools/` | Scripts that regenerate the screenshots and the document. |

The guide covers: sign-up, sign-in, creating a project, inviting members,
accepting an invitation, creating a task, marking a task done (assignee),
reviewing submissions (reviewer), completing a task (Lead), tracking
contributions, and writing the project wiki.

The screenshots are real: `tools/capture_screenshots.py` drove a running copy
of i-CONNECTS in headless Chromium through the scenario with made-up people
and data (Maya Chen, Liam Okafor, Sofia Ramirez, Kenji Watanabe; all
`@example.org`).

## Editing the guide

Edit `i-CONNECTS_User_Guide.docx` directly, then export the PDF:

- **Word:** right-click the table of contents › *Update Field* › *Update entire
  table*, then *File › Save As › PDF*.
- **LibreOffice:** *Tools › Update › Update All*, then *File › Export as PDF*.
- **Command line:** `/usr/bin/python3 documentation/tools/export_pdf.py`
  refreshes the table of contents and writes the PDF. It needs LibreOffice
  Writer and `python3-uno`.

To replace a screenshot, right-click the picture › *Change Picture* and pick
the new file from `screenshots/`.

## Regenerating everything after the app changes

`tools/build_guide.js` holds the guide's text and layout and rebuilds the
`.docx` from scratch, so it **overwrites hand edits** made to the `.docx`. Copy
any such edits into `build_guide.js` before running it.

1. Install the tools: Playwright for Python (`pip install playwright pillow`
   and `playwright install chromium`), Node with the `docx` package
   (`npm install docx`), and LibreOffice Writer with `python3-uno`.
2. With PostgreSQL and Redis running and `DATABASE_URL` / `REDIS_URL` set as
   for local development, run:

   ```bash
   documentation/tools/reset_and_capture.sh
   ```

   **This empties the database it points at** (`manage.py flush`) — use a
   throwaway database. It starts a dev server and a Celery worker with
   `tools/docs_settings.py` (emails are written to files so the script can
   follow the confirmation and invitation links), runs the scenario and
   re-saves every screenshot in `screenshots/`.
3. Build the document and the PDF:

   ```bash
   node documentation/tools/build_guide.js
   /usr/bin/python3 documentation/tools/export_pdf.py
   ```

`capture_screenshots.py` takes section numbers to re-run part of the scenario
(`s07 s08`), but each section expects the data the earlier ones create, so a
full run from an empty database is the reliable way. If the machine reaches
the internet only through a proxy that headless Chromium does not trust, see
the `NPM_CDN_DIR` and `HTTPS_CA_BUNDLE` options at the top of the script.
