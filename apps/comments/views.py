"""Comment views: add comment, mark read, delete."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.comments.forms import CommentForm
from apps.comments.models import Comment, CommentReadStatus
from apps.tasks.mixins import TaskMemberAccessMixin
from apps.tasks.models import Task, TaskLog


class CommentCreateView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """HTMX endpoint: append a comment to a TaskLog."""

    task_url_kwarg = "task_pk"

    def post(self, request, *args, **kwargs):
        if self.project_member is None:
            return HttpResponseBadRequest("No project membership.")
        task_log = get_object_or_404(
            TaskLog.objects.filter(task=self.task), pk=kwargs["log_pk"]
        )
        form = CommentForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "comments/comment_form.html",
                {
                    "form": form,
                    "task": self.task,
                    "task_log": task_log,
                },
                status=400,
            )
        comment: Comment = form.save(commit=False)
        comment.task_log = task_log
        comment.author = self.project_member
        comment.save()
        # Seed CommentReadStatus rows: author read=True, others read=False.
        CommentReadStatus.objects.get_or_create(
            comment=comment,
            project_member=self.project_member,
            defaults={"read": True},
        )
        return render(
            request,
            "comments/comment_item.html",
            {
                "comment": comment,
                "task": self.task,
                "task_log": task_log,
                "project_member": self.project_member,
            },
        )


class CommentMarkReadView(LoginRequiredMixin, View):
    """HTMX endpoint: mark a single comment as read by the current member."""

    def post(self, request, *args, **kwargs):
        comment = get_object_or_404(
            Comment.objects.select_related("task_log__task__project"), pk=kwargs["pk"]
        )
        project = comment.task_log.task.project
        from apps.tasks.mixins import ProjectContextMixin

        member = ProjectContextMixin._resolve_member(request.user, project)
        if member is None:
            return HttpResponseBadRequest("No project membership.")
        CommentReadStatus.objects.update_or_create(
            comment=comment,
            project_member=member,
            defaults={"read": True},
        )
        return HttpResponse(status=204)


class CommentDeleteView(LoginRequiredMixin, View):
    """Delete a comment (author or project manager only)."""

    def post(self, request, *args, **kwargs):
        comment = get_object_or_404(
            Comment.objects.select_related("task_log__task__project", "author"),
            pk=kwargs["pk"],
        )
        project = comment.task_log.task.project
        from apps.tasks.mixins import ProjectContextMixin

        member = ProjectContextMixin._resolve_member(request.user, project)
        is_pm = ProjectContextMixin._is_project_manager(request.user, project)
        is_author = member is not None and comment.author_id == member.id
        if not (is_author or is_pm):
            return HttpResponse(status=403)
        comment.delete()
        return HttpResponse(status=204)
