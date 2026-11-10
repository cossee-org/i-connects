"""Turn what happens in a project into messages for its Zulip topic.

The app already writes an audit row for everything worth announcing, so
that is the one place this listens: a new ``AuditLog`` row for a connected
project becomes a message. Three things the audit log does not record on
their own are picked up where they happen — a member added to an existing
task (the assignees relation), a comment (a new ``Comment``), and an
assignee marking their own part done (``tasks.services.mark_own_done``
calls :func:`apps.integrations.zulip.announce` itself).

Every receiver is best-effort: nothing here may fail the change it reports.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.comments.models import Comment
from apps.tasks.models import Task

from . import zulip

logger = logging.getLogger(__name__)

# How long after a task is created its first assignees still count as part
# of creating it (and are named in the "created" message, not their own).
_CREATION_WINDOW = timedelta(seconds=60)


def _task(log: AuditLog) -> Task | None:
    if log.entity_type != "task" or not log.entity_id:
        return None
    return Task.objects.filter(pk=log.entity_id).first()


def _task_created(log: AuditLog):
    # A CSV import creates tasks by the dozen; one message each would bury
    # the topic, so imports stay quiet.
    if (log.metadata or {}).get("via") == "csv_import":
        return None
    task = _task(log)
    if task is None:
        return None
    return zulip.TASKS, zulip.task_created_message(task, actor=log.actor)


def _task_completed(log: AuditLog):
    task = _task(log)
    if task is None:
        return None
    return zulip.TASKS, zulip.task_completed_message(task, actor=log.actor)


def _task_reviewed(log: AuditLog):
    from apps.projects.models import ProjectMember

    task = _task(log)
    if task is None:
        return None
    assignee = ProjectMember.objects.filter(
        pk=(log.metadata or {}).get("assignee_member_id")
    ).first()
    return zulip.TASKS, zulip.task_reviewed_message(
        task, actor=log.actor, assignee=assignee
    )


def _task_approved(log: AuditLog):
    task = _task(log)
    if task is None:
        return None
    return zulip.TASKS, zulip.task_approved_message(task, actor=log.actor)


def _member_added(log: AuditLog):
    from django.contrib.auth import get_user_model

    user = (
        get_user_model()
        .objects.filter(pk=(log.metadata or {}).get("target_user_id"))
        .first()
    )
    if user is None:
        return None
    return zulip.MEMBERS, zulip.member_joined_message(log.project, user=user)


def _wiki(verb: str):
    def build(log: AuditLog):
        from apps.wiki.models import WikiPage, WikiVisibility

        if log.entity_type != "wikipage" or not log.entity_id:
            return None
        page = WikiPage.objects.filter(pk=log.entity_id).first()
        # A leads-only page is not for the whole team's chat.
        if page is None or page.visibility == WikiVisibility.LEADS:
            return None
        # An edit that only changed the page's settings is not news.
        if (log.metadata or {}).get("text_changed") is False:
            return None
        return zulip.WIKI, zulip.wiki_message(page, actor=log.actor, verb=verb)

    return build


_BUILDERS = {
    AuditAction.TASK_CREATED.value: _task_created,
    AuditAction.TASK_COMPLETED.value: _task_completed,
    AuditAction.TASK_REVIEWED.value: _task_reviewed,
    AuditAction.TASK_APPROVED.value: _task_approved,
    AuditAction.MEMBER_ADDED.value: _member_added,
    AuditAction.WIKI_PAGE_CREATED.value: _wiki("created"),
    AuditAction.WIKI_PAGE_UPDATED.value: _wiki("edited"),
    AuditAction.WIKI_PAGE_RESTORED.value: _wiki("restored an earlier version of"),
}


@receiver(post_save, sender=AuditLog, dispatch_uid="zulip_audit_log")
def announce_audit_log(sender, instance: AuditLog, created: bool, **kwargs) -> None:
    if not created or instance.project_id is None:
        return
    builder = _BUILDERS.get(str(instance.action))
    if builder is None:
        return
    try:
        # Checked first so an unconnected project costs one query, not the
        # lookups a message needs.
        if zulip.connection_for(instance.project) is None:
            return
        built = builder(instance)
        if built is not None:
            group, content = built
            zulip.announce(instance.project, group, content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("zulip: could not announce %s: %s", instance.action, exc)


@receiver(
    m2m_changed,
    sender=Task.assigned_members.through,
    dispatch_uid="zulip_task_assigned",
)
def announce_task_assigned(sender, instance, action, reverse, pk_set, **kwargs) -> None:
    """Members added to an existing task. ``post_add`` carries only the
    members who were not on it already, so re-saving a task says nothing."""
    if action != "post_add" or reverse or not pk_set:
        return
    try:
        task = instance
        if zulip.connection_for(task.project, zulip.TASKS) is None:
            return
        # Assignees picked on the new-task form arrive before the task's
        # "created" audit row, whose message names them — don't say it twice.
        if (
            task.created_at > timezone.now() - _CREATION_WINDOW
            and not AuditLog.objects.filter(
                action=AuditAction.TASK_CREATED,
                entity_type="task",
                entity_id=task.pk,
            ).exists()
        ):
            return
        from apps.projects.models import ProjectMember

        members = list(ProjectMember.objects.filter(pk__in=pk_set))
        if members:
            zulip.announce(
                task.project, zulip.TASKS, zulip.task_assigned_message(task, members)
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("zulip: could not announce assignment: %s", exc)


@receiver(post_save, sender=Comment, dispatch_uid="zulip_comment")
def announce_comment(sender, instance: Comment, created: bool, **kwargs) -> None:
    if not created:
        return
    try:
        task = instance.task or (instance.task_log.task if instance.task_log_id else None)
        if task is None:
            return
        if zulip.connection_for(task.project, zulip.COMMENTS) is None:
            return
        zulip.announce(
            task.project, zulip.COMMENTS, zulip.comment_message(instance, task)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("zulip: could not announce comment: %s", exc)
