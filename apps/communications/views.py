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
from django.db.models import Q
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
    narrows it to one current member, ``?kind=`` to one kind of email, and
    ``?q=`` searches the recipient's name and address, the subject and the
    text of the email. ``?per_page=`` picks 25, 50 or 100 rows a page. A
    value that matches nothing is ignored rather than an error, and a page
    past the end shows the last one, so a stale link still opens the log.
    Each row's View button opens the email itself
    (``ProjectEmailDetailView``) in a modal.
    """

    template_name = "communications/email_log.html"
    context_object_name = "emails"
    paginate_by = 50
    PER_PAGE_CHOICES = (25, 50, 100)
    # Long enough for any address or subject line; a longer one is cut.
    MAX_QUERY_LENGTH = 200

    def get_template_names(self):
        # A live search swaps the results alone.
        if self.request.headers.get("HX-Request"):
            return ["communications/partials/email_log_results.html"]
        return [self.template_name]

    def get_paginate_by(self, queryset):
        try:
            per_page = int(self.request.GET.get("per_page", self.paginate_by))
        except (TypeError, ValueError):
            return self.paginate_by
        return per_page if per_page in self.PER_PAGE_CHOICES else self.paginate_by

    def paginate_queryset(self, queryset, page_size):
        """As ListView's, but a page that is not a number or is past the end
        opens the nearest page instead of a 404: narrowing the filters often
        leaves a page number in the address that no longer exists."""
        paginator = self.get_paginator(queryset, page_size)
        try:
            number = int(self.request.GET.get(self.page_kwarg) or 1)
        except (TypeError, ValueError):
            number = 1
        page = paginator.page(max(1, min(number, paginator.num_pages)))
        return paginator, page, page.object_list, page.has_other_pages()

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
        self.search_query = (self.request.GET.get("q") or "").strip()[
            : self.MAX_QUERY_LENGTH
        ]
        if self.search_query:
            q = self.search_query
            queryset = queryset.filter(
                Q(to_email__icontains=q)
                | Q(recipient__name__icontains=q)
                | Q(recipient__given_name__icontains=q)
                | Q(recipient__family_name__icontains=q)
                | Q(subject__icontains=q)
                | Q(text_body__icontains=q)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_htmx_request"] = bool(self.request.headers.get("HX-Request"))
        context["selected_member"] = self.selected_member
        context["selected_kind"] = self.selected_kind
        context["search_query"] = self.search_query
        context["is_filtered"] = bool(
            self.selected_member or self.selected_kind or self.search_query
        )
        context["current_per_page"] = context["paginator"].per_page
        # The dropdowns' options, as (value, label) strings.
        context["member_options"] = [("", "All members")] + [
            (str(u.pk), u.name or u.email) for u in self._members()
        ]
        context["selected_member_value"] = (
            str(self.selected_member.pk) if self.selected_member else ""
        )
        # A digest is about no one project, so it is never in a project's log.
        context["kind_options"] = [("", "All emails")] + [
            choice for choice in EmailKind.choices if choice[0] != EmailKind.DIGEST
        ]
        context["per_page_options"] = [(str(n), str(n)) for n in self.PER_PAGE_CHOICES]
        context["current_per_page_value"] = str(context["current_per_page"])
        context["results_summary"] = _results_summary(
            context["paginator"].count,
            member=self.selected_member,
            kind_label=EmailKind(self.selected_kind).label if self.selected_kind else "",
            q=self.search_query,
        )
        # The filters, carried across pages — only those set, so a link
        # does not carry the bar's empty hidden fields.
        params = self.request.GET.copy()
        params.pop("page", None)
        for key in [k for k, v in params.lists() if not any(v)]:
            del params[key]
        context["filter_query"] = params.urlencode()
        return context


def _results_summary(count, *, member, kind_label, q):
    """The filters in a sentence, as on People discovery — "3 emails to Bea
    Lund of type Task assigned matching “reef”" — or None when there are
    none."""
    if not (member or kind_label or q):
        return None
    summary = f"{count} email{'s' if count != 1 else ''}"
    if member is not None:
        summary += f" to {member.name or member.email}"
    if kind_label:
        summary += f" of type {kind_label}"
    if q:
        summary += f" matching “{q}”"
    return summary


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
