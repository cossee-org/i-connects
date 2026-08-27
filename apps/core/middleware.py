"""Development-only guards that turn confusing failures into clear ones."""

from __future__ import annotations

import html

from django.core.exceptions import MiddlewareNotUsed
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpResponse


class UnappliedMigrationsMiddleware:
    """Say "run migrate" instead of letting a missing column surface as a 500.

    The dev container mounts the repo and serves it with ``runserver``, so a
    ``git pull`` swaps the code under a running server while its database
    stays where it was. The next request then asks for a column the models
    know about and the schema does not, and Django reports it as a
    ``ProgrammingError`` naming one column — which says nothing about the
    migration that would add it.

    So: before the view runs, check whether anything is outstanding, and if
    it is, answer with what to run instead.

    Costs one query per process. Once the plan comes back empty it is not
    checked again — a migration applied later arrives with a code change,
    which restarts the process. While the plan is *not* empty the check
    repeats on every request, so the page clears the moment ``migrate``
    finishes, without a restart.

    Dropped entirely outside DEBUG: ``MiddlewareNotUsed`` takes it out of the
    chain at startup, so a deployment pays nothing for it.
    """

    def __init__(self, get_response):
        from django.conf import settings

        if not settings.DEBUG:
            raise MiddlewareNotUsed
        self.get_response = get_response
        self._verified = False

    def __call__(self, request):
        if not self._verified:
            pending = self._pending()
            if pending is None:
                # The database could not be reached, or the migration state
                # could not be read. Not our story to tell — let the request
                # through and let the real error speak.
                pass
            elif pending:
                return self._explain(pending)
            else:
                self._verified = True
        return self.get_response(request)

    @staticmethod
    def _pending() -> list[str] | None:
        """``"app.migration"`` names still to apply, or None if unknowable."""
        try:
            executor = MigrationExecutor(connections["default"])
            targets = executor.loader.graph.leaf_nodes()
            return [
                f"{migration.app_label}.{migration.name}"
                for migration, _backwards in executor.migration_plan(targets)
            ]
        except Exception:  # noqa: BLE001 — a broken DB is a different error
            return None

    @staticmethod
    def _explain(pending: list[str]) -> HttpResponse:
        rows = "\n".join(f"      <li><code>{html.escape(n)}</code></li>" for n in pending)
        return HttpResponse(
            f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unapplied migrations</title>
<style>
  body {{ font: 16px/1.6 system-ui, sans-serif; margin: 3rem auto; max-width: 42rem;
         padding: 0 1.5rem; color: #333; }}
  h1 {{ font-size: 1.4rem; color: #323895; }}
  code, pre {{ background: #eaeff8; border-radius: 4px; }}
  code {{ padding: .1rem .3rem; }}
  pre {{ padding: .75rem 1rem; overflow-x: auto; }}
  ul {{ padding-left: 1.2rem; }}
</style></head><body>
  <h1>The database is behind the code</h1>
  <p>
    {len(pending)} migration{'' if len(pending) == 1 else 's'} {'has' if len(pending) == 1 else 'have'}
    not been applied, so the models are asking for columns the schema does not
    have yet. Apply them:
  </p>
  <pre>docker compose exec web python manage.py migrate</pre>
  <p>Then reload this page &mdash; no restart needed.</p>
  <p>Outstanding:</p>
  <ul>
{rows}
  </ul>
  <p style="color:#666;font-size:.9rem">
    This page replaces the column-not-found error you would otherwise get. It
    is shown only when <code>DEBUG</code> is on.
  </p>
</body></html>""",
            status=503,
        )
