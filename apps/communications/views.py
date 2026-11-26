"""Views for the communications module.

The personal page in the sidebar is still a placeholder. A project's
Communications tab is open to the project's Leads only for now: there a
Lead connects the project to a Zulip topic, finds the link to it and can
post to it without leaving i-CONNECTS. What the project posts on its own
(tasks, deadlines, new members, comments, wiki edits) is
:mod:`apps.integrations.signals`' business, not this module's.

The project's email log (``ProjectEmailLogView``) is reached from the
Contributors tab and open to the project's Leads only, with each email's
full content in a modal (``ProjectEmailDetailView``).

Access mirrors the rest of the app: the personal page needs a signed-in
user; the project's Communications tab and its email log need the
project's Lead privilege (``ProjectManagerRequiredMixin`` answers 403
otherwise) and render the project tab strip.
"""

from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import DetailView, ListView, TemplateView, View

from apps.audit.services import AuditAction, log_action
from apps.integrations import zulip
from apps.integrations.models import ZulipConnection
from apps.projects.models import ProjectMember
from apps.tasks.mixins import ProjectContextMixin, ProjectManagerRequiredMixin

from .forms import TeamMessageForm, ZulipConnectionForm
from .models import EmailKind, EmailLog
from .services import logs_for_user


class CommunicationsView(LoginRequiredMixin, TemplateView):
    """Personal communications page — the sidebar module."""

    template_name = "communications/list.html"


def _connection(project) -> ZulipConnection | None:
    return ZulipConnection.objects.filter(project=project).first()


class _SectionBase(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, TemplateView
):
    """What every view of a project's Communications pages shares: Leads
    only, the page each draws (and re-renders when its own form is invalid)
    and its context.

    The tab itself shows the team chat once the project is connected to
    Zulip, and the connection form until then. The connection, once made,
    has a page of its own, opened from the tab's Zulip connection button.
    """

    template_name = "communications/project.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        connection = _connection(self.project)
        context["connection"] = connection
        context["topic_url"] = zulip.topic_url(connection) if connection else ""
        context.setdefault("message_form", TeamMessageForm())
        context.setdefault("connection_form", ZulipConnectionForm(instance=connection))
        return context

    def _url(self) -> str:
        return reverse(
            "communications:project", kwargs={"project_pk": self.project.pk}
        )


class ProjectCommunicationsView(_SectionBase):
    """A project's Communications tab (Leads only for now)."""


class ZulipConnectionView(_SectionBase):
    """The project's Zulip connection: see it, change it (Leads).

    A project not connected yet has no page here: its connection form is on
    the Communications tab itself, so a visit is sent there. Saving from
    either place posts here, and a form with errors is drawn again where it
    was filled in.
    """

    template_name = "communications/zulip_connection.html"

    def get(self, request, *args, **kwargs):
        if _connection(self.project) is None:
            return redirect(self._url())
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        connection = _connection(self.project)
        form = ZulipConnectionForm(request.POST, instance=connection)
        if form.is_valid():
            candidate = form.save(commit=False)
            candidate.project = self.project
            # Prove a new destination works before keeping it: the test
            # message doubles as the notice in the topic that the project
            # is now connected.
            ok, detail = True, ""
            if form.destination_changed():
                ok, detail = zulip.deliver(
                    candidate, zulip.connected_message(self.project)
                )
            if ok:
                candidate.save()
                messages.success(request, "Zulip connection saved.")
                return redirect(self._url())
            form.add_error(
                None,
                f"The test message could not be sent, so nothing was saved. {detail}",
            )
        if connection is None:
            self.template_name = _SectionBase.template_name
        return self.render_to_response(
            self.get_context_data(connection_form=form), status=400
        )


class ZulipDisconnectView(_SectionBase):
    """Remove the project's Zulip connection (Leads)."""

    def post(self, request, *args, **kwargs):
        ZulipConnection.objects.filter(project=self.project).delete()
        messages.success(request, "Zulip disconnected.")
        return redirect(self._url())


class CommunicationsTabView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
    """Show or hide the project's Communications tab (Leads), from the
    project's Settings. Hidden, the pages still answer, reached from
    Settings."""

    def post(self, request, *args, **kwargs):
        shown = request.POST.get("show_communications_tab") == "on"
        if self.project.show_communications_tab != shown:
            self.project.show_communications_tab = shown
            self.project.save(update_fields=["show_communications_tab", "updated_at"])
            log_action(
                AuditAction.PROJECT_UPDATED,
                actor=request.user,
                entity=self.project,
                project=self.project,
                metadata={
                    "name": self.project.name,
                    "show_communications_tab": shown,
                },
            )
        messages.success(
            request,
            "The Communications tab is shown."
            if shown
            else "The Communications tab is hidden. Open it from Settings.",
        )
        return redirect(reverse("projects:update", kwargs={"pk": self.project.pk}))


class TeamMessageView(_SectionBase):
    """Post a Lead's message to the project's Zulip topic."""

    def post(self, request, *args, **kwargs):
        connection = zulip.connection_for(self.project)
        if connection is None:
            messages.error(request, "This project is not connected to a team chat.")
            return redirect(self._url())
        form = TeamMessageForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(
                self.get_context_data(message_form=form), status=400
            )
        # Sent now rather than queued: the member should know whether their
        # message reached the team.
        ok, detail = zulip.deliver(
            connection,
            zulip.team_message(user=request.user, text=form.cleaned_data["text"]),
        )
        if ok:
            messages.success(request, "Message sent to the team chat.")
        else:
            messages.error(request, f"The message could not be sent. {detail}")
        return redirect(self._url())


class ProjectEmailLogView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, ListView
):
    """The emails i-CONNECTS sent about a project — Leads only.

    Newest first, one row per email per recipient. ``?member=<user id>``
    narrows it to one current member, ``?kind=`` to one kind of email, and
    ``?q=`` searches the recipient's name and address, the name and address
    of the member who triggered it, the subject and the text of the email. ``?per_page=`` picks 25, 50 or 100 rows a page. A
    value that matches nothing is ignored rather than an error, and a page
    past the end shows the last one, so a stale link still opens the log.
    Each row's View button opens the email itself
    (``ProjectEmailDetailView``) in a modal.
    """

    template_name = "communications/email_log.html"
    context_object_name = "emails"
    paginate_by = 50
    PER_PAGE_CHOICES = (25, 50, 100)
    # Kinds left out of the Email filter. A digest is about no one project,
    # so it is never in a project's log. Announcements and mailing list
    # messages are left out for now, as nothing in the app's navigation sends
    # them yet; they are still listed, and ``?kind=`` still narrows to them.
    KINDS_NOT_OFFERED = frozenset(
        {EmailKind.DIGEST, EmailKind.ANNOUNCEMENT, EmailKind.MAILING_LIST}
    )
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
            "recipient", "triggered_by"
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
                | Q(triggered_by__name__icontains=q)
                | Q(triggered_by__email__icontains=q)
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
        context["kind_options"] = [("", "All emails")] + [
            choice
            for choice in EmailKind.choices
            if choice[0] not in self.KINDS_NOT_OFFERED
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
            "recipient", "triggered_by"
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
