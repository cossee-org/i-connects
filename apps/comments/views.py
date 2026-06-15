"""Comment views: add comment, edit comment, mark read, delete."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.comments.forms import CommentForm
from apps.comments.models import Comment, CommentReadStatus
from apps.tasks.mixins import ProjectContextMixin, TaskMemberAccessMixin
from apps.tasks.models import Task, TaskLog


class CommentCreateView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """HTMX endpoint: append a task-level comment."""

    task_url_kwarg = "task_pk"

    def post(self, request, *args, **kwargs):
        if self.project_member is None:
            return HttpResponseBadRequest("No project membership.")
        form = CommentForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "comments/comment_form.html",
                {"form": form, "task": self.task},
                status=400,
            )
        comment: Comment = form.save(commit=False)
        comment.task = self.task
        comment.author = self.project_member
        comment.save()
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
                "project_member": self.project_member,
            },
        )


class CommentEditView(LoginRequiredMixin, View):
    """HTMX endpoint: inline-edit a comment (author only)."""

    def get(self, request, *args, **kwargs):
        comment = get_object_or_404(Comment, pk=kwargs["pk"])
        project = self._get_project(comment)
        member = ProjectContextMixin._resolve_member(request.user, project)
        if member is None or comment.author_id != member.id:
            return HttpResponse(status=403)
        if request.GET.get("cancel"):
            task = comment.task or (comment.task_log.task if comment.task_log else None)
            return render(
                request,
                "comments/comment_item.html",
                {"comment": comment, "task": task, "project_member": member},
            )
        form = CommentForm(instance=comment)
        return render(
            request,
            "comments/comment_edit_form.html",
            {"form": form, "comment": comment},
        )

    def post(self, request, *args, **kwargs):
        comment = get_object_or_404(Comment, pk=kwargs["pk"])
        project = self._get_project(comment)
        member = ProjectContextMixin._resolve_member(request.user, project)
        if member is None or comment.author_id != member.id:
            return HttpResponse(status=403)
        form = CommentForm(request.POST, instance=comment)
        if not form.is_valid():
            return render(
                request,
                "comments/comment_edit_form.html",
                {"form": form, "comment": comment},
                status=400,
            )
        form.save()
        task = comment.task or (comment.task_log.task if comment.task_log else None)
        return render(
            request,
            "comments/comment_item.html",
            {"comment": comment, "task": task, "project_member": member},
        )

    @staticmethod
    def _get_project(comment: Comment):
        if comment.task_id:
            return comment.task.project
        return comment.task_log.task.project


class CommentMarkReadView(LoginRequiredMixin, View):
    """HTMX endpoint: mark a single comment as read by the current member."""

    def post(self, request, *args, **kwargs):
        comment = get_object_or_404(
            Comment.objects.select_related("task__project", "task_log__task__project"),
            pk=kwargs["pk"],
        )
        if comment.task_id:
            project = comment.task.project
        else:
            project = comment.task_log.task.project
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
            Comment.objects.select_related("task__project", "task_log__task__project", "author"),
            pk=kwargs["pk"],
        )
        if comment.task_id:
            project = comment.task.project
        else:
            project = comment.task_log.task.project
        member = ProjectContextMixin._resolve_member(request.user, project)
        is_pm = ProjectContextMixin._is_project_manager(request.user, project)
        is_author = member is not None and comment.author_id == member.id
        if not (is_author or is_pm):
            return HttpResponse(status=403)
        comment.delete()
        return HttpResponse(status=204)
