"""Views for the communications module.

The personal page in the sidebar is still a placeholder. A project's
section is where the project meets its team chat: a Lead connects the
project to a Zulip topic there, and from then on every member finds the
link to that topic and can post to it without leaving i-CONNECTS. What the
project posts on its own (tasks, deadlines, new members, comments, wiki
edits) is :mod:`apps.integrations.signals`' business, not this module's.

Access mirrors the rest of the app: the personal page needs a signed-in
user, the project section needs project membership (``ProjectContextMixin``
raises 403 otherwise) and renders the project tab strip. Changing the
connection is for Leads.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from apps.integrations import zulip
from apps.integrations.models import ZulipConnection
from apps.tasks.mixins import ProjectContextMixin, ProjectManagerRequiredMixin

from .forms import TeamMessageForm, ZulipConnectionForm


class CommunicationsView(LoginRequiredMixin, TemplateView):
    """Personal communications page — the sidebar module."""

    template_name = "communications/list.html"


def _connection(project) -> ZulipConnection | None:
    return ZulipConnection.objects.filter(project=project).first()


class _SectionBase(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """What every view of a project's section shares: the page it draws
    (each re-renders it when its own form is invalid) and its context."""

    template_name = "communications/project.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        connection = _connection(self.project)
        context["connection"] = connection
        context["topic_url"] = zulip.topic_url(connection) if connection else ""
        context.setdefault("message_form", TeamMessageForm())
        if self.is_project_manager:
            context.setdefault(
                "connection_form", ZulipConnectionForm(instance=connection)
            )
        return context

    def _url(self) -> str:
        return reverse(
            "communications:project", kwargs={"project_pk": self.project.pk}
        )


class ProjectCommunicationsView(_SectionBase):
    """A project's communications section — the project tab."""


class ZulipConnectionSaveView(ProjectManagerRequiredMixin, _SectionBase):
    """Create or change the project's Zulip connection (Leads)."""

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
        return self.render_to_response(
            self.get_context_data(connection_form=form), status=400
        )


class ZulipDisconnectView(ProjectManagerRequiredMixin, _SectionBase):
    """Remove the project's Zulip connection (Leads)."""

    def post(self, request, *args, **kwargs):
        ZulipConnection.objects.filter(project=self.project).delete()
        messages.success(request, "Zulip disconnected.")
        return redirect(self._url())


class TeamMessageView(_SectionBase):
    """Post a member's message to the project's Zulip topic."""

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
