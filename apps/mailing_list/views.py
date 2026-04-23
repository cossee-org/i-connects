"""Views for the mailing list app.

- ``MailingListArchiveView`` — paginated archive of past messages (any member).
- ``MailingListDetailView`` — single archived message (any member).
- ``MailingListComposeView`` — compose form (project managers only).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import DetailView, FormView, ListView

from apps.tasks.mixins import ProjectContextMixin

from .forms import MailingListMessageForm
from .models import MailingListMessage
from .services import _collect_recipients, send_mailing_list_message


class MailingListArchiveView(LoginRequiredMixin, ProjectContextMixin, ListView):
    template_name = "mailing_list/archive.html"
    context_object_name = "messages_page"
    paginate_by = 20

    def get_queryset(self):
        return (
            MailingListMessage.objects.filter(project=self.project)
            .select_related("sender")
        )


class MailingListDetailView(LoginRequiredMixin, ProjectContextMixin, DetailView):
    template_name = "mailing_list/detail.html"
    context_object_name = "ml_message"

    def get_object(self, queryset=None):
        return get_object_or_404(
            MailingListMessage.objects.select_related("sender", "project"),
            pk=self.kwargs["pk"],
            project=self.project,
        )


class MailingListComposeView(LoginRequiredMixin, ProjectContextMixin, FormView):
    template_name = "mailing_list/compose.html"
    form_class = MailingListMessageForm

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # ``ProjectContextMixin.dispatch`` sets ``is_project_manager``; guard
        # after it runs so ``self.project`` is resolved first.
        return response

    def post(self, request, *args, **kwargs):
        if not getattr(self, "is_project_manager", False):
            raise PermissionDenied("Only project managers can send mailing-list messages.")
        return super().post(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if not getattr(self, "is_project_manager", False):
            raise PermissionDenied("Only project managers can send mailing-list messages.")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Preview count shown on the compose form — excludes the sender.
        recipients = _collect_recipients(
            self.project, exclude_user=self.request.user
        )
        context["recipient_count"] = len(recipients)
        return context

    def form_valid(self, form):
        message = send_mailing_list_message(
            project=self.project,
            sender=self.request.user,
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
        )
        messages.success(
            self.request,
            f"Sent to {message.recipient_count} member"
            f"{'' if message.recipient_count == 1 else 's'}.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "mailing_list:archive",
            kwargs={"project_pk": self.project.pk},
        )
