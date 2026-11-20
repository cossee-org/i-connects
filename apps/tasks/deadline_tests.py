"""Individual deadlines: a Lead gives one assignee, or every assignee not yet
done, a deadline of their own on a task (``AssigneeDeadline``).

The task deadline still holds for the task and everyone else. A task past
its deadline whose every assignee still working holds a later individual
deadline is *Extended* rather than overdue.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import EmailFrequency, MemberPrivileges, ProjectStatus, TaskStatus
from apps.notifications.models import Notification
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner
from apps.tasks.models import AssigneeDeadline, KanbanBoard, Task, TaskLog
from apps.tasks.services import (
    clear_member_deadline,
    mark_own_done,
    set_member_deadline,
    set_pending_deadlines,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_input(dt):
    """A datetime as the deadline picker (datetime-local) submits it."""
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


def _setup(lead_user, *others, deadline_days=-2):
    """A project led by ``lead_user`` with one task, due ``deadline_days``
    from now (past by default), assigned to a member for each of ``others``.
    Returns (project, task, lead_member, [members])."""
    project = create_project_with_owner(
        user=lead_user,
        form_data={"name": "Deadlines", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    lead = ProjectMember.objects.filter(project=project, users=lead_user).first()
    column = KanbanBoard.objects.filter(project=project).first() or KanbanBoard.objects.create(
        project=project, name="Todo", container_order=0
    )
    task = Task.objects.create(
        project=project,
        created_by=lead,
        container=column,
        container_task_order=0,
        name="Write chapter",
        deadline=timezone.now() + timedelta(days=deadline_days),
    )
    members = []
    for user in others:
        member = ProjectMember.objects.create(project=project)
        member.users.add(user)
        ProjectPrivilege.objects.create(
            project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
        )
        task.assigned_members.add(member)
        members.append(member)
    return project, task, lead, members


def _post(client, task, **data):
    return client.post(
        reverse("tasks:member_deadline", kwargs={"pk": task.pk}),
        {"pane_mode": "1", **data},
        HTTP_HX_REQUEST="true",
    )


# ---------------------------------------------------------------------------
# Setting, changing and removing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_lead_sets_an_individual_deadline_later_than_the_task(client, pm_user, other_user):
    _project, task, _lead, [bob] = _setup(pm_user, other_user)
    later = timezone.now() + timedelta(days=5)
    client.force_login(pm_user)

    response = _post(client, task, member=bob.pk, deadline=_local_input(later), reason="Conference")

    assert response.status_code == 200
    entry = AssigneeDeadline.objects.get(task=task, member=bob)
    assert entry.reason == "Conference"
    assert abs(entry.deadline - later) < timedelta(minutes=1)
    # The task deadline is untouched: it still holds for the task.
    task.refresh_from_db()
    assert task.deadline < timezone.now()
    assert task.deadline_for(bob) == entry.deadline
    # The swapped-in block shows the member's own deadline and the Change control.
    content = response.content.decode()
    assert 'data-member-deadline="own"' in content
    assert "own deadline" in content


@pytest.mark.django_db
def test_individual_deadline_may_be_earlier_than_the_task(client, pm_user, other_user):
    _project, task, _lead, [bob] = _setup(pm_user, other_user, deadline_days=10)
    earlier = timezone.now() + timedelta(days=2)
    client.force_login(pm_user)

    response = _post(client, task, member=bob.pk, deadline=_local_input(earlier))

    assert response.status_code == 200
    assert task.deadline_for(bob) < task.deadline


@pytest.mark.django_db
def test_individual_deadline_must_be_in_the_future(client, pm_user, other_user):
    _project, task, _lead, [bob] = _setup(pm_user, other_user)
    client.force_login(pm_user)

    response = _post(
        client, task, member=bob.pk,
        deadline=_local_input(timezone.now() - timedelta(days=1)),
    )

    assert response.status_code == 400
    assert "future" in response.content.decode()
    assert not AssigneeDeadline.objects.exists()


@pytest.mark.django_db
def test_only_a_lead_can_set_a_deadline(client, pm_user, user, other_user):
    project, task, _lead, [alice, _bob] = _setup(pm_user, user, other_user)
    client.force_login(user)

    response = _post(
        client, task, member=alice.pk,
        deadline=_local_input(timezone.now() + timedelta(days=3)),
    )

    assert response.status_code == 403
    assert not AssigneeDeadline.objects.exists()


@pytest.mark.django_db
def test_deadline_only_for_an_assignee(client, pm_user, user, other_user):
    project, task, _lead, [bob] = _setup(pm_user, other_user)
    outsider = ProjectMember.objects.create(project=project)
    outsider.users.add(user)
    client.force_login(pm_user)

    response = _post(
        client, task, member=outsider.pk,
        deadline=_local_input(timezone.now() + timedelta(days=3)),
    )

    assert response.status_code == 400
    assert not AssigneeDeadline.objects.exists()


@pytest.mark.django_db
def test_deadline_refused_on_a_completed_task(client, pm_user, other_user):
    _project, task, _lead, [bob] = _setup(pm_user, other_user)
    task.status = TaskStatus.COMPLETED
    task.save()
    client.force_login(pm_user)

    response = _post(
        client, task, member=bob.pk,
        deadline=_local_input(timezone.now() + timedelta(days=3)),
    )

    assert response.status_code == 403
    assert not AssigneeDeadline.objects.exists()


@pytest.mark.django_db
def test_lead_removes_an_individual_deadline(client, pm_user, other_user):
    _project, task, lead, [bob] = _setup(pm_user, other_user)
    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=3), lead=lead
    )
    client.force_login(pm_user)

    response = _post(client, task, member=bob.pk, action="clear")

    assert response.status_code == 200
    assert not AssigneeDeadline.objects.exists()
    assert task.deadline_for(bob) == task.deadline


@pytest.mark.django_db
def test_setting_and_removing_write_audit_rows_and_tell_the_member(pm_user, other_user):
    _project, task, lead, [bob] = _setup(pm_user, other_user)
    first = timezone.now() + timedelta(days=3)
    second = timezone.now() + timedelta(days=6)

    set_member_deadline(task=task, member=bob, deadline=first, lead=lead, reason="Ill")
    set_member_deadline(task=task, member=bob, deadline=second, lead=lead, reason="Ill")
    clear_member_deadline(task=task, member=bob, actor=lead)

    rows = list(
        AuditLog.objects.filter(
            action__in=[AuditAction.TASK_DEADLINE_SET, AuditAction.TASK_DEADLINE_CLEARED]
        ).order_by("id")
    )
    assert [r.action for r in rows] == [
        AuditAction.TASK_DEADLINE_SET,
        AuditAction.TASK_DEADLINE_SET,
        AuditAction.TASK_DEADLINE_CLEARED,
    ]
    assert rows[0].metadata["previous_deadline"] is None
    assert rows[0].metadata["reason"] == "Ill"
    assert rows[1].metadata["previous_deadline"] == first.isoformat()
    assert rows[2].metadata["previous_deadline"] == second.isoformat()
    assert rows[0].actor == pm_user

    notes = Notification.objects.for_user(other_user)
    assert notes.count() == 3
    assert not Notification.objects.for_user(pm_user).exists()


@pytest.mark.django_db
def test_setting_the_same_deadline_twice_writes_nothing_new(pm_user, other_user):
    _project, task, lead, [bob] = _setup(pm_user, other_user)
    when = timezone.now() + timedelta(days=3)
    set_member_deadline(task=task, member=bob, deadline=when, lead=lead)
    set_member_deadline(task=task, member=bob, deadline=when, lead=lead)
    assert AuditLog.objects.filter(action=AuditAction.TASK_DEADLINE_SET).count() == 1


@pytest.mark.django_db
def test_unassigning_drops_the_individual_deadline(pm_user, user, other_user):
    _project, task, lead, [alice, bob] = _setup(pm_user, user, other_user)
    for m in (alice, bob):
        set_member_deadline(
            task=task, member=m, deadline=timezone.now() + timedelta(days=3), lead=lead
        )

    task.assigned_members.set([alice])
    assert list(AssigneeDeadline.objects.values_list("member_id", flat=True)) == [alice.pk]

    bob.assigned_tasks.add(task)
    alice.assigned_tasks.remove(task)
    assert not AssigneeDeadline.objects.exists()


# ---------------------------------------------------------------------------
# Everyone not yet done
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_set_deadline_for_all_not_done_skips_members_done(client, pm_user, user, other_user):
    _project, task, _lead, [alice, bob] = _setup(pm_user, user, other_user)
    mark_own_done(task=task, member=alice)
    later = timezone.now() + timedelta(days=4)
    client.force_login(pm_user)

    response = _post(client, task, scope="pending", deadline=_local_input(later))

    assert response.status_code == 200
    assert list(AssigneeDeadline.objects.values_list("member_id", flat=True)) == [bob.pk]
    row = AuditLog.objects.get(action=AuditAction.TASK_DEADLINE_SET)
    assert row.metadata["batch"] == "pending"


@pytest.mark.django_db
def test_set_deadline_for_all_refused_when_everyone_is_done(client, pm_user, other_user):
    _project, task, _lead, [bob] = _setup(pm_user, other_user)
    mark_own_done(task=task, member=bob)
    client.force_login(pm_user)

    response = _post(
        client, task, scope="pending",
        deadline=_local_input(timezone.now() + timedelta(days=4)),
    )

    assert response.status_code == 400
    assert not AssigneeDeadline.objects.exists()


# ---------------------------------------------------------------------------
# Overdue vs Extended
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_task_is_extended_when_everyone_still_working_has_a_later_deadline(
    pm_user, user, other_user
):
    _project, task, lead, [alice, bob] = _setup(pm_user, user, other_user)
    later = timezone.now() + timedelta(days=3)

    assert task.is_overdue and not task.is_extended

    # One of two still working has an extension: still overdue.
    set_member_deadline(task=task, member=alice, deadline=later, lead=lead)
    assert task.is_overdue and not task.is_extended
    assert list(Task.objects.overdue()) == [task]
    assert not Task.objects.extended().exists()

    # The other one finishes: everyone still working is extended.
    mark_own_done(task=task, member=bob)
    assert task.is_extended and not task.is_overdue
    assert list(Task.objects.extended()) == [task]
    assert not Task.objects.overdue().exists()


@pytest.mark.django_db
def test_extension_that_has_itself_passed_makes_the_task_overdue_again(pm_user, other_user):
    _project, task, lead, [bob] = _setup(pm_user, other_user, deadline_days=-5)
    entry = set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=1), lead=lead
    )
    assert task.is_extended
    AssigneeDeadline.objects.filter(pk=entry.pk).update(
        deadline=timezone.now() - timedelta(days=1)
    )
    assert task.is_overdue
    assert list(Task.objects.overdue()) == [task]


@pytest.mark.django_db
def test_task_with_nobody_assigned_or_everyone_done_stays_overdue(pm_user, other_user):
    _project, task, _lead, [bob] = _setup(pm_user, other_user)
    mark_own_done(task=task, member=bob)
    assert task.is_overdue
    task.assigned_members.clear()
    assert task.is_overdue
    assert list(Task.objects.overdue()) == [task]


@pytest.mark.django_db
def test_earlier_individual_deadline_does_not_make_the_task_overdue(pm_user, other_user):
    _project, task, lead, [bob] = _setup(pm_user, other_user, deadline_days=10)
    entry = set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=1), lead=lead
    )
    AssigneeDeadline.objects.filter(pk=entry.pk).update(
        deadline=timezone.now() - timedelta(hours=1)
    )
    assert not task.is_overdue and not task.is_extended
    # ...but the member is late on theirs.
    assert list(Task.objects.overdue_for(other_user)) == [task]


@pytest.mark.django_db
def test_board_shows_extended_and_filters_it(client, pm_user, other_user):
    project, task, lead, [bob] = _setup(pm_user, other_user)
    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=3), lead=lead
    )
    client.force_login(pm_user)
    kanban = reverse("tasks:board_kanban", kwargs={"project_pk": project.pk})

    content = client.get(kanban).content.decode()
    assert "data-extended-badge" in content
    assert 'aria-label="Overdue"' not in content

    assert "Write chapter" in client.get(kanban, {"filter": "extended"}).content.decode()
    assert "Write chapter" not in client.get(kanban, {"filter": "overdue"}).content.decode()


@pytest.mark.django_db
def test_project_overview_does_not_count_an_extended_task_overdue(client, pm_user, other_user):
    project, task, lead, [bob] = _setup(pm_user, other_user)
    client.force_login(pm_user)
    url = reverse("projects:detail", kwargs={"pk": project.pk})
    assert client.get(url).context["overdue_count"] == 1

    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=3), lead=lead
    )
    assert client.get(url).context["overdue_count"] == 0


# ---------------------------------------------------------------------------
# The member's own views use their own deadline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dashboard_overdue_follows_the_users_own_deadline(client, pm_user, user, other_user):
    _project, task, lead, [alice, bob] = _setup(pm_user, user, other_user)
    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=3), lead=lead
    )
    dashboard = reverse("projects:dashboard")

    client.force_login(other_user)
    response = client.get(dashboard, {"type": "overdue"})
    assert response.context["active_tasks_match_count"] == 0
    content = client.get(dashboard).content.decode()
    assert "(yours)" in content

    client.force_login(user)
    response = client.get(dashboard, {"type": "overdue"})
    assert response.context["active_tasks_match_count"] == 1


@pytest.mark.django_db
def test_my_tasks_shows_and_sorts_by_the_users_own_deadline(client, pm_user, other_user):
    project, task, lead, [bob] = _setup(pm_user, other_user, deadline_days=20)
    later_task = Task.objects.create(
        project=project, created_by=lead, container=task.container,
        container_task_order=1, name="Review draft",
        deadline=timezone.now() + timedelta(days=5),
    )
    later_task.assigned_members.add(bob)
    # Bob's own deadline on the first task comes before the second task's.
    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=2), lead=lead
    )
    client.force_login(other_user)

    response = client.get(reverse("tasks:my_list"), {"sort": "deadline_asc"})

    names = [t.name for t in response.context["tasks"]]
    assert names == ["Write chapter", "Review draft"]
    assert "(your deadline)" in response.content.decode()


@pytest.mark.django_db
def test_digest_lists_only_tasks_past_the_users_own_deadline(pm_user, user, other_user):
    from apps.notifications.tasks import _overdue_tasks_for

    _project, task, lead, [alice, bob] = _setup(pm_user, user, other_user)
    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=3), lead=lead
    )
    assert _overdue_tasks_for(other_user) == []
    assert _overdue_tasks_for(user) == [task]


@pytest.mark.django_db
def test_overdue_reminder_skips_members_on_a_later_deadline(
    monkeypatch, pm_user, user, other_user
):
    from cron.tasks import overdue_task_reminder_cron

    for u in (user, other_user):
        u.email_overdue_task_frequency = EmailFrequency.DAILY
        u.save()
    _project, task, lead, [alice, bob] = _setup(pm_user, user, other_user)
    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=3), lead=lead
    )
    queued = []
    monkeypatch.setattr(
        "apps.notifications.tasks.send_digest_email_task.delay",
        lambda uid, freq: queued.append(uid),
    )

    overdue_task_reminder_cron()

    assert queued == [user.pk]


@pytest.mark.django_db
def test_due_hint_speaks_to_the_viewers_own_deadline(client, pm_user, other_user):
    project, task, lead, [bob] = _setup(pm_user, other_user, deadline_days=10)
    set_member_deadline(
        task=task, member=bob, deadline=timezone.now() + timedelta(days=3), lead=lead
    )
    url = reverse("tasks:detail_pane", kwargs={"project_pk": project.pk, "pk": task.pk})

    client.force_login(other_user)
    assert "Your deadline" in client.get(url).content.decode()

    client.force_login(pm_user)
    content = client.get(url).content.decode()
    assert "Your deadline" not in content
    assert "days left" in content


# ---------------------------------------------------------------------------
# Lead controls on the completion table
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_lead_sees_deadline_controls_and_member_does_not(client, pm_user, other_user):
    project, task, _lead, [bob] = _setup(pm_user, other_user)
    url = reverse("tasks:detail_pane", kwargs={"project_pk": project.pk, "pk": task.pk})

    client.force_login(pm_user)
    content = client.get(url).content.decode()
    assert f'data-set-deadline="{bob.pk}"' in content
    assert "data-deadline-all" in content
    assert "data-deadline-modal" in content
    # Bob's part is late on the task deadline.
    assert "data-member-overdue" in content

    client.force_login(other_user)
    content = client.get(url).content.decode()
    assert "data-set-deadline" not in content
    assert "data-deadline-modal" not in content
