"""Service helpers for the tasks app.

Small, compact helpers used by the HTMX views. They keep view bodies
focused on request/response handling and concentrate the mutation logic
(ordering, TaskLog audit entries, approval state) here.
"""

from __future__ import annotations

from typing import Iterable

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
    task.closed_by = completed_by
    task.closed_at = timezone.now()
    task.save(
        update_fields=["status", "closed_by", "closed_at", "updated_at"]
    )

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
    the assignees' marks, the reviews and the Lead's verifications all
    survive, and so does the credit those verifications wrote. Credit stands
    on a Lead verifying each part, not on the task's status.

    Who closed it goes: the completion it named no longer stands, and the
    next close writes its own.
    """
    task.status = TaskStatus.NOT_COMPLETED
    task.closed_by = None
    task.closed_at = None
    task.save(
        update_fields=["status", "closed_by", "closed_at", "updated_at"]
    )
    return task


def review_locked(task: Task, log: TaskLog | None) -> bool:
    """True when ``log``'s review was given before ``task`` was completed.

    Such a review was part of what stood when the Lead marked the task
    completed, so it stays as it is until a Lead reopens the task: it cannot
    be withdrawn, and the assignee cannot undo the done mark it rests on
    (that would clear it). A review given after the task was completed is not
    locked, and can be withdrawn as usual.
    """
    return bool(
        task.status == TaskStatus.COMPLETED
        and task.closed_at is not None
        and log is not None
        and log.reviewed_at is not None
        and log.reviewed_at <= task.closed_at
    )


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

    The mark is the assignee's statement about their own work, so it stays
    open after a Lead closes the task: on a task with many assignees, the
    Lead often closes it before everyone has ticked. Marking earns no credit
    by itself; that waits on a Lead verifying the part (:func:`verify_part`).

    Every change of the mark writes an audit row, so who marked whose part
    done, and when, survives the TaskLog being updated in place.
    """
    log = _current_log_for(task, member)
    if log is None:
        log = TaskLog(
            task=task, assigned_to=member, completed_as=CompletedAs.INDIVIDUAL
        )
    was_done = log.pk is not None and log.status == TaskStatus.COMPLETED
    log.status = TaskStatus.COMPLETED
    log.completed_by = actor or member
    log.completed_as = CompletedAs.INDIVIDUAL
    log.completed_at = timezone.now()
    log.save()
    if not was_done:
        _log_mark_action(
            AuditAction.TASK_PART_DONE, task_log=log, actor_member=actor or member
        )
    return log


@transaction.atomic
def reopen_own(
    *, task: Task, member: ProjectMember, actor: ProjectMember | None = None
) -> TaskLog | None:
    """Undo a single assignee's INDIVIDUAL-mode completion.

    A review and a verification are verdicts on a submission, so withdrawing
    the submission withdraws both with it: the names and dates are cleared
    rather than left standing over work that is no longer marked done, and
    the credit the verification wrote goes too. If the member marks their
    part done again it comes back unreviewed and unverified, waiting on a
    fresh look.

    ``actor`` is who clicked, used for the audit rows; it defaults to
    ``member``, the assignee undoing their own mark.

    Like marking, undoing stays open on a closed task, and never reopens it:
    the task's completion is the Lead's call, not a sum of the marks.
    """
    log = _current_log_for(task, member)
    if log is None:
        return None
    was_done = log.status == TaskStatus.COMPLETED
    had_review = log.is_reviewed or log.reviewed_by_id is not None
    had_verification = log.is_verified or log.verified_by_id is not None
    log.status = TaskStatus.NOT_COMPLETED
    log.completed_at = None
    log.reviewed_by = None
    log.reviewed_at = None
    log.verified_by = None
    log.verified_at = None
    log.save(
        update_fields=[
            "status",
            "completed_at",
            "reviewed_by",
            "reviewed_at",
            "verified_by",
            "verified_at",
        ]
    )
    if had_review:
        _log_review_action(
            AuditAction.TASK_REVIEW_CLEARED,
            task_log=log,
            actor_member=actor or member,
            reason="the done mark it stood on was reverted",
        )
    if had_verification:
        _log_verify_action(
            AuditAction.TASK_PART_UNVERIFIED,
            task_log=log,
            actor_member=actor or member,
            reason="the done mark it stood on was reverted",
        )
    _withdraw_member_contributions(task, member)
    if was_done:
        _log_mark_action(
            AuditAction.TASK_PART_UNDONE, task_log=log, actor_member=actor or member
        )
    return log


@transaction.atomic
def close_task(
    *, task: Task, closed_by: ProjectMember, reason: str | None = None
) -> Task:
    """Close a task, finalising ``Task.status`` to COMPLETED.

    A Lead's action, always allowed: a Lead can close a task whenever they
    judge it done, whether or not every assignee has marked their own part.
    Nothing closes a task on its own; reviews in particular do not.

    Only the task's own status moves. Per-assignee TaskLogs are left exactly
    as they are in *both* completion modes: "marked done" is an assignee's
    statement about their own work, and nothing a Lead does writes one on
    their behalf. So an outstanding assignee stays outstanding rather than
    being credited with work they never marked — the gap the All tasks view
    surfaces as "Closed without your mark". In TEAM mode, where assignees
    have no self-mark action at all, that means a closed task simply carries
    no individual marks.

    Closing writes no credit. On a project tracking contributions as CRediT,
    credit is written per member as a Lead verifies their part
    (:func:`verify_part`), before or after the task is closed.

    The task records who closed it and when, so the page can name them
    without going to the audit log.
    """
    closer_user = closed_by.users.first()
    task.status = TaskStatus.COMPLETED
    task.closed_by = closed_by
    task.closed_at = timezone.now()
    task.save(
        update_fields=["status", "closed_by", "closed_at", "updated_at"]
    )
    metadata = {"name": task.name}
    if reason:
        metadata["reason"] = reason
    log_action(
        AuditAction.TASK_COMPLETED,
        actor=closer_user,
        entity=task,
        project=task.project,
        metadata=metadata,
    )
    _notify_task_completed(task=task, completed_by=closed_by)
    return task


def _record_member_contribution(task_log: TaskLog, *, verifier_user=None) -> None:
    """Write one verified part's credit, late-imported to dodge a cycle."""
    from apps.contributions.services import record_member_contribution

    record_member_contribution(task_log, verifier_user=verifier_user)


def _withdraw_member_contributions(task: Task, member: ProjectMember) -> None:
    """Take back one member's credit on ``task``, late-imported likewise."""
    from apps.contributions.services import withdraw_member_contributions

    withdraw_member_contributions(task, member)


def all_submissions_reviewed(task: Task) -> bool:
    """True when every assignee has marked their part done and had it reviewed.

    Read from the task's *current* assignees and their current TaskLog, so it
    follows the same live sets the completion matrix does: a member added to
    the task brings an unreviewed row with them, and one taken off stops being
    counted.

    False for a task nobody is assigned to, and for a task that asks for no
    review: there are no verdicts there for "all of them" to describe.
    """
    if not task.needs_review:
        return False
    current = {}
    for log in task.task_logs.all():  # newest-first; first seen is current
        current.setdefault(log.assigned_to_id, log)
    # Read off the prefetched set rather than values_list, which would issue a
    # query of its own on every render of the completion matrix.
    assignee_ids = [member.pk for member in task.assigned_members.all()]
    if not assignee_ids:
        return False
    for member_id in assignee_ids:
        log = current.get(member_id)
        if log is None or log.status != TaskStatus.COMPLETED or not log.is_reviewed:
            return False
    return True


@transaction.atomic
def mark_reviewed(*, task_log: TaskLog, reviewer: ProjectMember) -> TaskLog:
    """Record ``reviewer``'s sign-off on one assignee's submission.

    Peer review is deliberately separate from a Lead's verification
    (:func:`verify_part`), which is what writes credit: a review is a
    verdict a reviewer can withdraw and an assignee can invalidate by
    reverting their mark. Stamping who and when is all this does, so the
    reversal in :func:`clear_review` really is complete.

    Callers are responsible for checking that ``reviewer`` may review this
    task and that the submission exists (see ``views.TaskLogReviewView``).

    Open whether or not the task is completed, and it changes neither the
    task's status nor anyone's credit: closing is a Lead's call, and credit
    waits on a Lead's verification (:func:`verify_part`).
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

    Open on a completed task for a review given after it was completed; one
    given before stays locked (see :func:`review_locked`, which callers
    check). It leaves the task's status, the Lead's verification and the
    credit alone: verification does not rest on the review (see
    :func:`verify_part`).
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


@transaction.atomic
def verify_part(*, task_log: TaskLog, lead: ProjectMember) -> TaskLog:
    """A Lead verifies one member's contribution, crediting them for it.

    This is the ledger event: on a project tracking contributions as CRediT
    it writes the member's ContributionRecord, verified by ``lead``. It is
    open whether or not the task is completed and does not need a review:
    a Lead may verify a part the reviewers have not reached.

    A no-op on a part already verified, so a double click writes nothing
    twice. Callers check that ``lead`` is a Lead, that the part is marked
    done and that its member is still on the task (see
    ``views.TaskLogVerifyView``).
    """
    if task_log.is_verified:
        return task_log
    task_log.verified_by = lead
    task_log.verified_at = timezone.now()
    task_log.save(update_fields=["verified_by", "verified_at"])
    _log_verify_action(
        AuditAction.TASK_PART_VERIFIED,
        task_log=task_log,
        actor_member=lead,
    )
    _record_member_contribution(task_log, verifier_user=lead.users.first())
    return task_log


@transaction.atomic
def unverify_part(*, task_log: TaskLog, actor: ProjectMember | None) -> TaskLog:
    """Withdraw a Lead's verification, and the credit it wrote.

    Leaves the done mark and any review alone. A no-op on a part that is not
    verified.
    """
    if not task_log.is_verified and task_log.verified_by_id is None:
        return task_log
    task_log.verified_by = None
    task_log.verified_at = None
    task_log.save(update_fields=["verified_by", "verified_at"])
    _log_verify_action(
        AuditAction.TASK_PART_UNVERIFIED, task_log=task_log, actor_member=actor
    )
    _withdraw_member_contributions(task_log.task, task_log.assigned_to)
    return task_log


def _log_verify_action(
    action,
    *,
    task_log: TaskLog,
    actor_member: ProjectMember | None,
    reason: str | None = None,
) -> None:
    """Append an audit row for a verification set or withdrawn on ``task_log``."""
    task = task_log.task
    metadata = {
        "task_name": task.name,
        "assignee_member_id": task_log.assigned_to_id,
        "task_log_id": task_log.pk,
        "task_completed": task.status == TaskStatus.COMPLETED,
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


def _log_mark_action(
    action, *, task_log: TaskLog, actor_member: ProjectMember | None
) -> None:
    """Append an audit row for a done mark set or undone on ``task_log``.

    ``after_completion`` flags a mark changed on a task already closed, so
    marks made after the Lead closed it can be told apart.
    """
    task = task_log.task
    log_action(
        action,
        actor=actor_member.users.first() if actor_member is not None else None,
        entity=task,
        project=task.project,
        metadata={
            "task_name": task.name,
            "assignee_member_id": task_log.assigned_to_id,
            "task_log_id": task_log.pk,
            "after_completion": task.status == TaskStatus.COMPLETED,
        },
    )


@transaction.atomic
def approve_completion(
    *,
    task_log: TaskLog,
    approved: bool | None,
    verifier_user=None,
) -> TaskLog:
    """Set approval state on a TaskLog.

    A lead's verdict on one assignee's submission, recorded and announced.
    It does not touch the contributions ledger: a Lead verifying the part is
    the ledger event (see :func:`verify_part`).
    """
    task_log.approved = approved
    task_log.save(update_fields=["approved"])

    if approved is True:
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
def _queue_assignment_email(user_id: int, task_ids: list[int], assigned_by_id):
    """Return an ``on_commit`` callback that queues one assignment email.

    A named function, not ``functools.partial``: when a robust callback
    raises, Django logs it by ``func.__qualname__``, which a partial lacks,
    so the logging itself raised and the "robust" callback failed the
    request anyway.
    """
    from apps.tasks.tasks import send_task_assigned_email_task

    def queue_task_assigned_email() -> None:
        send_task_assigned_email_task.delay(user_id, task_ids, assigned_by_id)

    return queue_task_assigned_email


def notify_new_assignees(
    assignments: Iterable[tuple[Task, Iterable[int]]], *, assigned_by=None
) -> None:
    """Tell members they were just assigned a task, in-app and by email.

    ``assignments`` pairs each task with the ``ProjectMember`` ids *newly*
    added to it — callers compare assignees before and after the change, so
    re-saving a task does not tell its existing assignees again. A team
    member reaches each of its users. ``assigned_by`` (a user) is left out:
    a Lead assigning themselves needs no word of it.

    Each task gets its own in-app notification. The email goes once per
    person, listing every task they were given here, so a CSV import that
    hands someone ten tasks sends one email rather than ten. It is queued
    for after the surrounding transaction commits, so the worker sees the
    saved assignment; whether to send at all is the recipient's
    ``email_task_assigned`` preference, read by the task itself.
    """
    from apps.notifications.services import create_notification

    task_ids_by_user: dict[int, list[int]] = {}
    for task, member_ids in assignments:
        member_ids = set(member_ids)
        if not member_ids:
            continue
        user_ids = set(
            ProjectMember.objects.filter(pk__in=member_ids).values_list(
                "users", flat=True
            )
        )
        user_ids.discard(None)  # a member with no users left on it
        if assigned_by is not None:
            user_ids.discard(assigned_by.pk)
        if not user_ids:
            continue
        create_notification(
            users=list(user_ids),
            title=f"Task assigned to you: {task.name}",
            body=f"In project {task.project.name}.",
            project=task.project,
            route="tasks:detail",
            route_kwargs={"project_pk": task.project_id, "pk": task.pk},
        )
        for user_id in user_ids:
            task_ids_by_user.setdefault(user_id, []).append(task.pk)

    assigned_by_id = assigned_by.pk if assigned_by is not None else None
    for user_id, task_ids in task_ids_by_user.items():
        transaction.on_commit(
            _queue_assignment_email(user_id, task_ids, assigned_by_id),
            # A broker that is down must not fail an assignment already saved.
            robust=True,
        )


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
        route="tasks:detail",
        route_kwargs={"project_pk": task.project_id, "pk": task.pk},
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
        route="tasks:detail",
        route_kwargs={"project_pk": task.project_id, "pk": task.pk},
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
        route="tasks:detail",
        route_kwargs={"project_pk": task.project_id, "pk": task.pk},
    )
