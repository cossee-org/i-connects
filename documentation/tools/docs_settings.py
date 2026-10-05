"""Settings for capturing the user-guide screenshots.

Development settings, except that outgoing email is written to files in
``DOCS_MAIL_DIR`` so the capture script can read the sign-up confirmation and
invitation emails (and photograph them) instead of scraping the console.
"""

import os

from config.settings.dev import *  # noqa: F401,F403

EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
EMAIL_FILE_PATH = os.environ.get("DOCS_MAIL_DIR", "/tmp/iconnects-docs-mail")
