"""Service helpers for the tasks app.

Small, compact helpers used by the HTMX views. They keep view bodies
focused on request/response handling and concentrate the mutation logic
(ordering, TaskLog audit entries, approval state) here.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.services import AuditAction, log_action
from apps.core.enums import CompletedAs, TaskStatus
from apps.projects.models import ProjectMember
from apps.tasks.models import ActivityEvent, KanbanBoard, Task, TaskActivity, TaskLog


@transaction.atomic
def move_task(task: Task, *, container_id: int, order: int) -> Task:
    """Move ``task`` to ``container_id`` at position ``order``.

    Rewrites ``container_task_order`` on sibling tasks in both the source
    and destination columns so each column ends up densely numbered
    ``0, 1, 2, ...`` in the intended sequence.
    """

    new_container = KanbanBoard.objects.select_for_update().get(
        pk=container_id, project=task.project
    )
    source_container_id = task.container_id
    # Pull sibling ids (excluding the moving task) in current order.
    if new_container.id == source_container_id:
        siblings = list(
            Task.objects.filter(container_id=new_container.id)
            .exclude(pk=task.pk)
            .order_by("container_task_order", "id")
            .values_list("id", flat=True)
        )
        order = max(0, min(order, len(siblings)))
        siblings.insert(order, task.pk)
        for idx, tid in enumerate(siblings):
            Task.objects.filter(pk=tid).update(
                container_task_order=idx, container_id=new_container.id
            )
    else:
        # Renumber source column (excluding moving task).
        source_ids = list(
            Task.objects.filter(container_id=source_container_id)
            .exclude(pk=task.pk)
            .order_by("container_task_order", "id")
            .values_list("id", flat=True)
        )
        for idx, tid in enumerate(source_ids):
            Task.objects.filter(pk=tid).update(container_task_order=idx)
        # Insert into destination column at ``order``.
        dest_ids = list(
            Task.objects.filter(container_id=new_container.id)
            .order_by("container_task_order", "id")
            .values_list("id", flat=True)
        )
        order = max(0, min(order, len(dest_ids)))
        dest_ids.insert(order, task.pk)
        for idx, tid in enumerate(dest_ids):
            Task.objects.filter(pk=tid).update(
                container_task_order=idx, container_id=new_container.id
            )

    task.refresh_from_db()
    return task


@transaction.atomic
def complete_task(
    *,
    task: Task,
    completed_by: ProjectMember,
    completed_as: str = CompletedAs.INDIVIDUAL,
) -> TaskLog:
    """Append a COMPLETED TaskLog entry and flip Task.status.

    Creates one TaskLog per currently-assigned ProjectMember so the audit
    trail keeps its row-per-assignee shape. If no assignees exist, a
    single log is recorded against ``completed_by``.
    """

    assignees = list(task.assigned_members.all())
    if not assignees:
        assignees = [completed_by]

    logs = []
    for assignee in assignees:
        log = TaskLog.objects.create(
            task=task,
            assigned_to=assignee,
            completed_by=completed_by,
            status=TaskStatus.COMPLETED,
            completed_as=completed_as,
            completed_at=timezone.now(),
        )
        logs.append(log)

    task.status = TaskStatus.COMPLETED
    task.save(update_fields=["status", "updated_at"])

    completer_user = completed_by.users.first()
    log_action(
        AuditAction.TASK_COMPLETED,
        actor=completer_user,
        entity=task,
        project=task.project,
        metadata={"name": task.name},
    )

    _notify_task_completed(task=task, completed_by=completed_by)

    # Return the log entry that actually represents the caller, if present.
    for log in logs:
        if log.assigned_to_id == completed_by.id:
            return log
    return logs[0]


@transaction.atomic
def reopen_task(*, task: Task) -> Task:
    """Reopen a closed task (Lead action), flipping ``Task.status`` back.

    The mirror of :func:`close_task`, and status-only for the same reason:
    the assignees' own marks survive, so a Lead toggling a task closed and
    open again does not wipe what they recorded.
    """
    task.status = TaskStatus.NOT_COMPLETED
    task.save(update_fields=["status", "updated_at"])
    return task


def _current_log_for(task: Task, member: ProjectMember) -> TaskLog | None:
    """The member's most-recent TaskLog on this task (their current state)."""
    return (
        task.task_logs.filter(assigned_to=member).order_by("-created_at").first()
    )


@transaction.atomic
def mark_own_done(
    *, task: Task, member: ProjectMember, actor: ProjectMember | None = None
) -> TaskLog:
    """Mark a single assignee's part of an INDIVIDUAL-mode task done.

    Updates the member's current TaskLog in place (or seeds one) and leaves
    ``Task.status`` untouched — closing the whole task is a separate, Lead-only
    action handled by :func:`close_task`.

    ``actor`` is who clicked, recorded as ``completed_by``; it defaults to
    ``member``, the assignee marking their own part. A Lead marking someone
    else done passes themselves, so the entry says who really set it rather
    than crediting the assignee with a click they never made.
    """
    log = _current_log_for(task, member)
    if log is None:
        log = TaskLog(
            task=task, assigned_to=member, completed_as=CompletedAs.INDIVIDUAL
        )
    log.status = TaskStatus.COMPLETED
    log.completed_by = actor or member
    log.completed_as = CompletedAs.INDIVIDUAL
    log.completed_at = timezone.now()
    log.save()
    return log


@transaction.atomic
def reopen_own(
    *, task: Task, member: ProjectMember, actor: ProjectMember | None = None
) -> TaskLog | None:
    """Undo a single assignee's INDIVIDUAL-mode completion.

    A review is a verdict on a submission, so withdrawing the submission
    withdraws the review with it: the reviewer's name and date are cleared
    rather than left standing over work that is no longer marked done. If the
    member marks their part done again it comes back unreviewed, waiting on a
    fresh look.

    ``actor`` is who clicked, used for the audit row; it defaults to
    ``member``, the assignee undoing their own mark.
    """
    log = _current_log_for(task, member)
    if log is None:
        return None
    had_review = log.is_reviewed or log.reviewed_by_id is not None
    log.status = TaskStatus.NOT_COMPLETED
    log.completed_at = None
    log.reviewed_by = None
    log.reviewed_at = None
    log.save(
        update_fields=["status", "completed_at", "reviewed_by", "reviewed_at"]
    )
    if had_review:
        _log_review_action(
            AuditAction.TASK_REVIEW_CLEARED,
            task_log=log,
            actor_member=actor or member,
            reason="the done mark it stood on was reverted",
        )
    return log


@transaction.atomic
def close_task(*, task: Task, closed_by: ProjectMember) -> Task:
    """Close a task (Lead action), finalising ``Task.status`` to COMPLETED.

    Always allowed: a Lead can close a task whenever they judge it done,
    whether or not every assignee has marked their own part.

    Only the task's own status moves. Per-assignee TaskLogs are left exactly
    as they are in *both* completion modes: "marked done" is an assignee's
    statement about their own work, and nothing a Lead does writes one on
    their behalf. So an outstanding assignee stays outstanding rather than
    being credited with work they never marked — the gap the All tasks view
    surfaces as "Closed without your mark". In TEAM mode, where assignees
    have no self-mark action at all, that means a closed task simply carries
    no individual marks.

    This does not write contributions — the separate PM approval flow remains
    the ledger event.
    """
    task.status = TaskStatus.COMPLETED
    task.save(update_fields=["status", "updated_at"])
    log_action(
        AuditAction.TASK_COMPLETED,
        actor=closed_by.users.first(),
        entity=task,
        project=task.project,
        metadata={"name": task.name},
    )
    _notify_task_completed(task=task, completed_by=closed_by)
    return task


@transaction.atomic
def mark_reviewed(*, task_log: TaskLog, reviewer: ProjectMember) -> TaskLog:
    """Record ``reviewer``'s sign-off on one assignee's submission.

    Peer review is deliberately separate from :func:`approve_completion`: an
    approval writes an immutable ContributionRecord, while a review is a
    verdict a reviewer can withdraw and an assignee can invalidate by
    reverting their mark. Stamping who and when is all this does, so the
    reversal in :func:`clear_review` really is complete.

    Callers are responsible for checking that ``reviewer`` may review this
    task and that the submission exists (see ``views.TaskLogReviewView``).
    """
    task_log.reviewed_by = reviewer
    task_log.reviewed_at = timezone.now()
    task_log.save(update_fields=["reviewed_by", "reviewed_at"])
    _log_review_action(
        AuditAction.TASK_REVIEWED, task_log=task_log, actor_member=reviewer
    )
    _notify_submission_reviewed(task_log=task_log, reviewer=reviewer)
    return task_log


@transaction.atomic
def clear_review(
    *, task_log: TaskLog, cleared_by: ProjectMember | None = None
) -> TaskLog:
    """Withdraw the review on a submission, leaving the assignee's mark alone.

    A no-op on an unreviewed entry, so a double-clicked Undo is harmless.
    """
    if not task_log.is_reviewed and task_log.reviewed_by_id is None:
        return task_log
    task_log.reviewed_by = None
    task_log.reviewed_at = None
    task_log.save(update_fields=["reviewed_by", "reviewed_at"])
    _log_review_action(
        AuditAction.TASK_REVIEW_CLEARED,
        task_log=task_log,
        actor_member=cleared_by,
    )
    return task_log


def _log_review_action(
    action,
    *,
    task_log: TaskLog,
    actor_member: ProjectMember | None,
    reason: str | None = None,
) -> None:
    """Append an audit row for a review event on ``task_log``."""
    task = task_log.task
    metadata = {
        "task_name": task.name,
        "assignee_member_id": task_log.assigned_to_id,
        "task_log_id": task_log.pk,
    }
    if reason:
        metadata["reason"] = reason
    log_action(
        action,
        actor=actor_member.users.first() if actor_member is not None else None,
        entity=task,
        project=task.project,
        metadata=metadata,
    )


@transaction.atomic
def approve_completion(
    *,
    task_log: TaskLog,
    approved: bool | None,
    verifier_user=None,
) -> TaskLog:
    """Set approval state on a TaskLog.

    When ``approved is True`` this is the *closure* event for the task per
    Ultra Plan §8, and a ``ContributionRecord`` is written for each user
    behind ``task_log.assigned_to``. The ledger call is idempotent, so
    toggling approval back and forth will not produce duplicates.
    """
    task_log.approved = approved
    task_log.save(update_fields=["approved"])

    if approved is True:
        # Late import to avoid circular imports with apps.contributions.
        from apps.contributions.services import record_contributions_for_approval

        record_contributions_for_approval(task_log, verifier_user=verifier_user)
        log_action(
            AuditAction.TASK_APPROVED,
            actor=verifier_user,
            entity=task_log.task,
            project=task_log.task.project,
            metadata={"task_name": task_log.task.name},
        )
        try:
            from apps.integrations.discord import announce_task_approved

            announce_task_approved(task_log.task, verifier=verifier_user)
        except Exception:  # noqa: BLE001 — never let webhook failure break approval
            pass
    elif approved is False:
        log_action(
            AuditAction.TASK_REJECTED,
            actor=verifier_user,
            entity=task_log.task,
            project=task_log.task.project,
            metadata={"task_name": task_log.task.name},
        )

    _notify_task_approval(task_log=task_log, approved=approved)

    return task_log


# ---------------------------------------------------------------------------
# Notification helpers (late-imported to avoid circular imports).
# ---------------------------------------------------------------------------
def _notify_task_completed(*, task: Task, completed_by: ProjectMember) -> None:
    try:
        from apps.notifications.services import create_notification
    except Exception:  # pragma: no cover
        return

    user_ids = set()
    for member in task.assigned_members.all():
        user_ids.update(member.users.values_list("id", flat=True))
    # Notify the task creator too so they see progress on their task.
    user_ids.update(task.created_by.users.values_list("id", flat=True))
    # Don't notify the person who just clicked "complete".
    user_ids.difference_update(completed_by.users.values_list("id", flat=True))
    if not user_ids:
        return

    create_notification(
        users=list(user_ids),
        title=f"Task completed: {task.name}",
        body=f"In project {task.project.name}.",
        project=task.project,
    )


def _notify_submission_reviewed(*, task_log: TaskLog, reviewer: ProjectMember) -> None:
    """Tell the assignee (and only them) that their submission was reviewed."""
    try:
        from apps.notifications.services import create_notification
    except Exception:  # pragma: no cover
        return

    user_ids = set(task_log.assigned_to.users.values_list("id", flat=True))
    # A reviewer who is also an assignee doesn't need to hear it from us.
    user_ids.difference_update(reviewer.users.values_list("id", flat=True))
    if not user_ids:
        return

    task = task_log.task
    create_notification(
        users=list(user_ids),
        title=f"Submission reviewed: {task.name}",
        body=f"{reviewer.display_name} marked your part reviewed.",
        project=task.project,
    )


def _notify_task_approval(*, task_log: TaskLog, approved: bool | None) -> None:
    try:
        from apps.notifications.services import create_notification
    except Exception:  # pragma: no cover
        return

    if approved is None:
        return

    assignee = task_log.assigned_to
    user_ids = list(assignee.users.values_list("id", flat=True))
    if not user_ids:
        return

    task = task_log.task
    verb = "approved" if approved else "rejected"
    create_notification(
        users=user_ids,
        title=f"Task completion {verb}: {task.name}",
        body=f"In project {task.project.name}.",
        project=task.project,
    )
