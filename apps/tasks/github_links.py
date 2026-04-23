"""GitHub issue / PR URL parsing helpers.

Pattern-based only: no network calls to github.com. Used to render a
compact label (``owner/repo#123``) on task detail pages when a task has
a ``github_url``.
"""

from __future__ import annotations

import re
from typing import Optional

_GITHUB_ISSUE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<kind>issues|pull)/(?P<number>\d+)",
    re.IGNORECASE,
)


def parse_github_url(url: str) -> Optional[dict]:
    """Return {owner, repo, kind, number, short} if url is a recognisable
    GitHub issue / PR URL, else None. Does not call the API."""
    if not url:
        return None
    m = _GITHUB_ISSUE_RE.match(url.strip())
    if not m:
        return None
    return {
        "owner": m.group("owner"),
        "repo": m.group("repo"),
        "kind": m.group("kind").lower(),  # "issues" or "pull"
        "number": int(m.group("number")),
        "short": f"{m.group('owner')}/{m.group('repo')}#{m.group('number')}",
    }
