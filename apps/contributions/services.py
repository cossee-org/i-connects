"""Service helpers for the contributions ledger.

Closing a task is the ledger event: :func:`record_contributions_for_task`
writes the credit, :func:`withdraw_contributions_for_task` takes it back when
the task reopens. The read side (``get_user_contributions``,
``get_project_contributions``, and the matrix builders) is kept separate so
the ledger could later be extracted or replaced.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.audit.services import AuditAction, log_action
from apps.core.enums import CREDIT_ROLES, ContributionType, TaskStatus
from apps.projects.models import ContributionTracking, Project, ProjectMember
from apps.tasks.models import Task, TaskLog

from .models import ContributionRecord

User = get_user_model()


def eligible_contributions(task: Task) -> list[tuple[ProjectMember, TaskLog]]:
    """``(assignee, task_log)`` for everyone who has earned credit on ``task``.

    An assignee qualifies once their own current TaskLog says COMPLETED —
    the mark is theirs to make, so nobody else's click stands in for it. A
    task that requires review asks one thing more: that submission has to
    have been signed off, since there the reviewers' verdict is what says the
    work landed.

    Read off the task's *live* assignee and reviewer sets, so a member taken
    off the task stops qualifying and one added brings an unmarked row with
    them.
    """
    current: dict[int, TaskLog] = {}
    for log in task.task_logs.all():  # newest-first; first seen is current
        current.setdefault(log.assigned_to_id, log)

    needs_review = task.needs_review
    eligible: list[tuple[ProjectMember, TaskLog]] = []
    for member in task.assigned_members.all():
        log = current.get(member.pk)
        if log is None or log.status != TaskStatus.COMPLETED:
            continue
        if needs_review and not log.is_reviewed:
            continue
        eligible.append((member, log))
    return eligible


@transaction.atomic
def record_contributions_for_task(
    task: Task,
    *,
    verifier_user: Optional[User] = None,
) -> list[ContributionRecord]:
    """Write the credit a closed task earned, one row per contributing user.

    Called from ``tasks.services.close_task``. Only projects tracking
    contributions as CRediT keep a ledger, so this is a no-op elsewhere.

    Idempotent: the ``(task_log, user)`` unique constraint plus
    ``get_or_create`` means re-closing a task writes no second row.

    ``verifier_user`` is the lead who closed the task, stored as
    ``verified_by``. It may be ``None`` when closure came from a code path
    with no user behind it (a data migration, an admin action) — the FK is
    nullable for that reason.
    """
    if not task.project.tracks_credit:
        return []

    closed_at = timezone.now()
    created: list[ContributionRecord] = []
    for member, task_log in eligible_contributions(task):
        for user in member.users.all():
            obj, was_created = ContributionRecord.objects.get_or_create(
                task=task,
                user=user,
                defaults=dict(
                    task_log=task_log,
                    project=task.project,
                    contribution_type=task.contribution_type,
                    verified_by=verifier_user,
                    closed_at=closed_at,
                ),
            )
            if was_created:
                created.append(obj)
    return created


@transaction.atomic
def withdraw_contributions_for_task(task: Task) -> int:
    """Take back the credit a task's closure wrote, and say how many rows went.

    The mirror of :func:`record_contributions_for_task`, called when a task
    reopens. Credit is a claim that a piece of work is finished; a reopened
    task is not, so the claim goes with it rather than standing over work
    that has been pulled back.

    A record a lead entered by hand stays. It was never derived from the
    task's state, so the task's state changing says nothing about it —
    removing it is the lead's own call.
    """
    deleted, _by_model = ContributionRecord.objects.filter(
        task=task, added_by_hand=False
    ).delete()
    return deleted


def get_user_contributions(
    user: User,
    *,
    project: Optional[Project] = None,
):
    """Return a user's contributions, optionally scoped to one project."""
    qs = ContributionRecord.objects.filter(user=user).select_related(
        "task", "project", "verified_by"
    )
    if project is not None:
        qs = qs.filter(project=project)
    return qs


def get_project_contributions(project: Project):
    """All contributions for a project, newest first."""
    return (
        ContributionRecord.objects.filter(project=project)
        .select_related("task", "user", "verified_by")
    )


def get_user_contribution_counts_by_type(
    user: User, *, project: Optional[Project] = None
) -> dict[str, int]:
    """A user's contribution counts by type, optionally scoped to one project."""
    qs = ContributionRecord.objects.filter(user=user)
    if project is not None:
        qs = qs.filter(project=project)
    counts: dict[str, int] = defaultdict(int)
    for ctype in qs.values_list("contribution_type", flat=True):
        counts[ctype] += 1
    # Return a plain dict keyed by the enum value.
    return dict(counts)


def get_user_credit_projects(user: User) -> list[Project]:
    """Projects where this user could hold credit — theirs, tracking CRediT.

    The filter's option list. Offering every project they belong to would
    list ones that can never show a row; offering only the ones they have
    already earned in would hide the answer to "have I been credited on
    this project at all?".
    """
    return list(
        Project.objects.filter(
            project_members__users=user,
            project_members__deleted=False,
            contribution_tracking=ContributionTracking.CREDIT,
        )
        .distinct()
        .order_by("name")
    )


def get_user_contributions_matrix(
    user: User,
    *,
    project: Optional[Project] = None,
    all_types: bool = False,
) -> dict:
    """Build a projects × CRediT-role matrix of one user's own credit.

    The same shape as :func:`get_project_contributions_matrix`, with
    ``rows`` keyed on ``"project"`` rather than ``"user"``. Columns are the
    roles this user has been credited with, or the whole taxonomy when
    ``all_types`` is set.
    """
    labels = dict(ContributionType.choices)
    qs = ContributionRecord.objects.filter(user=user)
    if project is not None:
        qs = qs.filter(project=project)
    records = list(
        qs.select_related("project").only(
            "project_id", "contribution_type", "project__name"
        )
    )

    used_types = {rec.contribution_type for rec in records}
    if all_types:
        type_values = list(CREDIT_ROLES) + sorted(
            v for v in used_types if v not in CREDIT_ROLES
        )
    else:
        type_values = [v for v in CREDIT_ROLES if v in used_types] + sorted(
            v for v in used_types if v not in CREDIT_ROLES
        )
    type_choices = [(v, labels.get(v, v)) for v in type_values]

    projects_by_id: dict[int, Project] = {}
    per_project: dict[int, dict[str, int]] = {}
    column_totals: dict[str, int] = {v: 0 for v in type_values}
    grand_total = 0

    for rec in records:
        projects_by_id.setdefault(rec.project_id, rec.project)
        counts = per_project.setdefault(
            rec.project_id, {v: 0 for v in type_values}
        )
        counts[rec.contribution_type] += 1
        column_totals[rec.contribution_type] += 1
        grand_total += 1

    rows = [
        {
            "project": projects_by_id[pid],
            "counts": counts,
            "total": sum(counts.values()),
        }
        for pid, counts in per_project.items()
    ]
    rows.sort(key=lambda r: (-r["total"], r["project"].name.lower()))

    column_totals["grand_total"] = grand_total
    return {"types": type_choices, "rows": rows, "totals": column_totals}


def project_member_users(project: Project) -> list[User]:
    """Every user holding a live membership on ``project``, name-sorted."""
    return list(
        User.objects.filter(
            project_memberships__project=project,
            project_memberships__deleted=False,
        )
        .distinct()
        .order_by("given_name", "family_name", "name", "email")
    )


def get_project_contributions_matrix(
    project: Project, *, only_used: bool = False
) -> dict:
    """Build a members × CRediT-role matrix for a project.

    Returns a dict with:
        - ``types``:  list of ``(value, label)`` for the columns.
        - ``rows``:   list of ``{"user": User, "counts": {type: int},
                      "total": int}``, sorted by total desc then by name.
        - ``totals``: ``{type: int}`` column totals, plus ``"grand_total"``.
                      Underscore-free keys so templates can reach them.

    By default the grid is the whole picture: every CRediT role across the
    top whether or not this project has used it, and every current member
    down the side whether or not they have earned anything. A zero is worth
    reading — it says which roles the project has yet to cover and who has
    yet to be credited — and a grid whose shape depends on the data is hard
    to compare against another project's.

    ``only_used`` trims it to what has been earned: roles with a non-zero
    column, members with a non-zero row. The page offers it as a filter for
    when the full grid is mostly empty.

    Either way, a role off the taxonomy that the project *has* used (a task
    left as "Other", say) keeps its column, and anyone who earned credit
    before leaving the project keeps their row.
    """
    labels = dict(ContributionType.choices)

    records = list(
        ContributionRecord.objects.filter(project=project)
        .select_related("user")
        .only(
            "user_id",
            "contribution_type",
            "user__name",
            "user__email",
            "user__given_name",
            "user__family_name",
        )
    )

    used_types = {rec.contribution_type for rec in records}
    # CRediT's own roles in the taxonomy's order, then anything off it this
    # project has used.
    type_values = list(CREDIT_ROLES) + sorted(
        v for v in used_types if v not in CREDIT_ROLES
    )

    users_by_id: dict[int, User] = {u.pk: u for u in project_member_users(project)}
    per_user: dict[int, dict[str, int]] = {
        uid: {v: 0 for v in type_values} for uid in users_by_id
    }
    column_totals: dict[str, int] = {v: 0 for v in type_values}
    grand_total = 0

    for rec in records:
        users_by_id.setdefault(rec.user_id, rec.user)
        counts = per_user.setdefault(rec.user_id, {v: 0 for v in type_values})
        counts[rec.contribution_type] += 1
        column_totals[rec.contribution_type] += 1
        grand_total += 1

    if only_used:
        type_values = [v for v in type_values if column_totals[v]]

    rows = [
        {
            "user": users_by_id[uid],
            "counts": {v: counts.get(v, 0) for v in type_values},
            "total": sum(counts.values()),
        }
        for uid, counts in per_user.items()
    ]
    if only_used:
        rows = [row for row in rows if row["total"]]
    rows.sort(key=lambda r: (-r["total"], user_sort_key(r["user"])))

    totals = {v: column_totals[v] for v in type_values}
    totals["grand_total"] = grand_total
    return {
        "types": [(v, labels.get(v, v)) for v in type_values],
        "rows": rows,
        "totals": totals,
    }


def user_sort_key(user: User) -> str:
    """Name a user sorts under: their full name, else their email."""
    full = f"{(user.given_name or '').strip()} {(user.family_name or '').strip()}".strip()
    return (full or user.name or user.email or "").lower()


def user_display_name(user: User) -> str:
    """A user's full name, falling back to their account name, then email."""
    full = f"{(user.given_name or '').strip()} {(user.family_name or '').strip()}".strip()
    return full or user.name or user.email


# ---------------------------------------------------------------------------
# Corrections — a lead's hand on the ledger
#
# Credit is a claim about who did what, so a wrong claim has to be fixable:
# a role recorded from the wrong task type, someone credited who should not
# have been, someone missed who should have been. Each correction writes an
# audit row naming who made it.
# ---------------------------------------------------------------------------


@transaction.atomic
def update_contribution(
    record: ContributionRecord,
    *,
    contribution_type: str,
    actor=None,
) -> ContributionRecord:
    """Correct the role a contribution is recorded under."""
    previous = record.contribution_type
    if previous == contribution_type:
        return record
    record.contribution_type = contribution_type
    record.save(update_fields=["contribution_type", "updated_at"])
    _log_contribution_action(
        AuditAction.CONTRIBUTION_UPDATED,
        record,
        actor=actor,
        extra={"from": previous, "to": contribution_type},
    )
    return record


@transaction.atomic
def remove_contribution(record: ContributionRecord, *, actor=None) -> None:
    """Withdraw a contribution a lead judges shouldn't stand."""
    _log_contribution_action(AuditAction.CONTRIBUTION_REMOVED, record, actor=actor)
    record.delete()


@transaction.atomic
def add_contribution(
    *,
    task: Task,
    user: User,
    contribution_type: str,
    actor=None,
) -> ContributionRecord:
    """Credit ``user`` on ``task`` by hand.

    For work the automatic writer missed — someone who did the work without
    being an assignee, or whose mark never went in. The record carries no
    submission, because there is none behind it: it stands on the lead's
    say-so, which is what ``added_by_hand`` records.

    Idempotent against the one-credit-per-person-per-task rule: crediting
    someone who already holds credit on the task corrects the role of the
    row they have rather than raising.
    """
    record = ContributionRecord.objects.filter(task=task, user=user).first()
    if record is not None:
        return update_contribution(
            record, contribution_type=contribution_type, actor=actor
        )

    record = ContributionRecord.objects.create(
        task=task,
        task_log=None,
        user=user,
        project=task.project,
        contribution_type=contribution_type,
        verified_by=actor if getattr(actor, "pk", None) else None,
        closed_at=timezone.now(),
        added_by_hand=True,
    )
    _log_contribution_action(AuditAction.CONTRIBUTION_ADDED, record, actor=actor)
    return record


def _log_contribution_action(action, record, *, actor=None, extra=None) -> None:
    metadata = {
        "task_name": record.task.name,
        "user_id": record.user_id,
        "contribution_type": record.contribution_type,
    }
    if extra:
        metadata.update(extra)
    log_action(
        action,
        actor=actor if getattr(actor, "pk", None) else None,
        entity=record.task,
        project=record.project,
        metadata=metadata,
    )
