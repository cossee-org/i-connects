"""Views for the communications module.

The two module pages are placeholders: both render an empty state. What
exists is the shape — a personal page in the sidebar and a section on each
project — so the routes, the navigation entries and the access rules are
settled before there is anything to show.

The one working surface is the project's email log (``ProjectEmailLogView``),
reached from the Contributors tab and open to the project's Leads only, with
each email's full content in a modal (``ProjectEmailDetailView``).

Access mirrors the rest of the app: the personal page needs a signed-in
user, the project section needs project membership (``ProjectContextMixin``
raises 403 otherwise) and renders the project tab strip.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, ListView, TemplateView

from apps.projects.models import ProjectMember
from apps.tasks.mixins import ProjectContextMixin, ProjectManagerRequiredMixin

from .models import EmailKind, EmailLog
from .services import logs_for_user


class CommunicationsView(LoginRequiredMixin, TemplateView):
    """Personal communications page — the sidebar module."""

    template_name = "communications/list.html"


class ProjectCommunicationsView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """A project's communications section — the project tab."""

    template_name = "communications/project.html"


class ProjectEmailLogView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, ListView
):
    """The emails i-connects sent about a project — Leads only.

    Newest first, one row per email per recipient. ``?member=<user id>``
    narrows it to one current member, ``?kind=`` to one kind of email; a
    value that matches nothing is ignored rather than an error, so a stale
    link still opens the log. Each row's View button opens the email itself
    (``ProjectEmailDetailView``) in a modal.
    """

    template_name = "communications/email_log.html"
    context_object_name = "emails"
    paginate_by = 50

    def _members(self):
        """The project's current members' accounts, by name."""
        if not hasattr(self, "_member_users"):
            self._member_users = list(
                get_user_model()
                .objects.filter(
                    pk__in=ProjectMember.objects.filter(
                        project=self.project, deleted=False
                    ).values("users")
                )
                .order_by("name", "email")
            )
        return self._member_users

    def get_queryset(self):
        queryset = EmailLog.objects.filter(project=self.project).select_related(
            "recipient"
        )
        self.selected_member = next(
            (u for u in self._members() if str(u.pk) == self.request.GET.get("member")),
            None,
        )
        if self.selected_member is not None:
            queryset = logs_for_user(queryset, self.selected_member)
        kind = self.request.GET.get("kind")
        self.selected_kind = kind if kind in EmailKind.values else ""
        if self.selected_kind:
            queryset = queryset.filter(kind=self.selected_kind)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["members"] = self._members()
        context["selected_member"] = self.selected_member
        context["selected_kind"] = self.selected_kind
        # A digest is about no one project, so it is never in a project's log.
        context["kind_choices"] = [
            choice for choice in EmailKind.choices if choice[0] != EmailKind.DIGEST
        ]
        # The filters, carried across pages.
        params = self.request.GET.copy()
        params.pop("page", None)
        context["filter_query"] = params.urlencode()
        return context


class ProjectEmailDetailView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, DetailView
):
    """One email in a project's log, as the recipient got it — Leads only.

    Rendered as a modal fragment, loaded by HTMX into the email log's
    dialog. An email from another project, or about no project, is a 404.
    """

    context_object_name = "email"

    def get_template_names(self):
        # The modal for the log's View email button; a page of its own for
        # the same link followed without scripting.
        if self.request.headers.get("HX-Request"):
            return ["communications/partials/email_detail_modal.html"]
        return ["communications/email_detail.html"]

    def get_queryset(self):
        return EmailLog.objects.filter(project=self.project).select_related(
            "recipient"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["html_srcdoc"] = _links_open_in_new_tab(self.object.html_body)
        return context


_HEAD_TAG = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)


def _links_open_in_new_tab(html: str) -> str:
    """``html`` with a ``<base target="_blank">``, so a link clicked in the
    sandboxed preview opens in a new tab rather than inside the frame.

    Put inside ``<head>`` when there is one, so a document with a doctype
    keeps it first and is not rendered in quirks mode.
    """
    if not html:
        return ""
    base = '<base target="_blank">'
    match = _HEAD_TAG.search(html)
    if match:
        return html[: match.end()] + base + html[match.end() :]
    return base + html
