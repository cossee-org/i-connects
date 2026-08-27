"""Views for the communications module.

The module is a placeholder: both surfaces render an empty state, and there
is no model behind them yet. What exists is the shape — a personal page in
the sidebar and a section on each project — so the routes, the navigation
entries and the access rules are settled before there is anything to show.

Access mirrors the rest of the app: the personal page needs a signed-in
user, the project section needs project membership (``ProjectContextMixin``
raises 403 otherwise) and renders the project tab strip.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.tasks.mixins import ProjectContextMixin


class CommunicationsView(LoginRequiredMixin, TemplateView):
    """Personal communications page — the sidebar module."""

    template_name = "communications/list.html"


class ProjectCommunicationsView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """A project's communications section — the project tab."""

    template_name = "communications/project.html"
