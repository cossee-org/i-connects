"""Smoke tests for the projects app."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import JoinRequestStatus, MemberPrivileges, ProjectStatus, TaskStatus
from apps.projects.models import (
    Invitation,
    JoinRequest,
    Project,
    ProjectMember,
    ProjectPrivilege,
)
from apps.projects.services import (
    JoinRequestError,
    LastProjectManagerError,
    approve_join_request,
    assert_pm_self_action_allowed,
    cancel_join_request,
    create_invitation,
    create_join_request,
    create_project_with_owner,
    decline_join_request,
    is_last_project_manager,
)


@pytest.mark.django_db
def test_public_list_is_anonymous_accessible(client):
    response = client.get(reverse("projects:list"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_my_list_requires_login(client):
    response = client.get(reverse("projects:my_list"))
    # Anonymous -> redirect to login.
    assert response.status_code in (301, 302)
    assert "/accounts/login" in response["Location"]


@pytest.mark.django_db
def test_my_list_htmx_search_returns_results_partial_only(client, user):
    create_project_with_owner(
        user=user,
        form_data={"name": "Alpha Findable", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    create_project_with_owner(
        user=user,
        form_data={"name": "Beta Other", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    client.force_login(user)

    response = client.get(
        reverse("projects:my_list"), {"q": "Findable"}, HTTP_HX_REQUEST="true"
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Alpha Findable" in content
    assert "Beta Other" not in content
    # The partial doesn't re-render the page shell (sidebar, filter form, etc.).
    assert 'id="filter-form-my"' not in content
    assert 'aria-label="Sidebar navigation"' not in content


@pytest.mark.django_db
def test_my_list_shows_open_due_and_join_request_counts(client, user, other_user):
    from datetime import timedelta

    from django.utils import timezone

    from apps.projects.models import ProjectMember
    from apps.tasks.models import KanbanBoard, Task

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Counted", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    column = KanbanBoard.objects.filter(project=project).first()
    member = ProjectMember.objects.filter(project=project, users=user).first()

    Task.objects.create(
        project=project,
        created_by=member,
        container=column,
        container_task_order=0,
        name="Open task",
    )
    Task.objects.create(
        project=project,
        created_by=member,
        container=column,
        container_task_order=1,
        name="Overdue task",
        deadline=timezone.now() - timedelta(days=1),
    )
    Task.objects.create(
        project=project,
        created_by=member,
        container=column,
        container_task_order=2,
        name="Completed task",
        status=TaskStatus.COMPLETED,
        deadline=timezone.now() - timedelta(days=1),
    )
    create_join_request(project=project, user=other_user, requested_privilege="CONTRIBUTOR")

    client.force_login(user)
    response = client.get(reverse("projects:my_list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "2 open tasks" in content
    assert "1 task overdue" in content
    assert "1 join request" in content


@pytest.mark.django_db
def test_create_project_makes_creator_a_pm(client_logged_in, user):
    response = client_logged_in.post(
        reverse("projects:create"),
        {
            "name": "Test Project",
            "description": "A project",
            "abstract": "",
            "keywords": "",
            "status": ProjectStatus.PLANNING,
            "is_public": "on",
            "public_summary": "",
        },
    )
    # Redirects to detail on success.
    assert response.status_code in (301, 302)
    project = Project.objects.get(name="Test Project")
    # Membership + PM privilege exist.
    assert ProjectMember.objects.filter(project=project, users=user).exists()
    assert ProjectPrivilege.objects.filter(
        project=project,
        user=user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.PROJECT_CREATED, actor=user, project=project
    ).exists()


@pytest.mark.django_db
def test_create_project_seeds_default_kanban_columns(user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Seeded", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    columns = list(project.containers.order_by("container_order"))
    assert [c.name for c in columns] == ["To Do", "In Progress", "Done"]
    assert [c.container_order for c in columns] == [0, 1, 2]


@pytest.mark.django_db
def test_only_pm_can_edit_project(client, user, other_user):
    # User A creates a project (PM).
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P1", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    # User B is a contributor, not a PM.
    member = ProjectMember.objects.create(project=project)
    member.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )

    client.force_login(other_user)
    response = client.get(
        reverse("projects:update", kwargs={"pk": project.pk})
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_invitation_code_is_unique_urlsafe(user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Inv", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    invitation = create_invitation(
        project=project,
        email="new@test.com",
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by="alice",
    )
    code = invitation.invitation_code
    assert isinstance(code, str)
    assert len(code) >= 20
    # URL-safe: only these characters.
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    )
    assert set(code).issubset(allowed)
    assert AuditLog.objects.filter(
        action=AuditAction.INVITATION_SENT,
        entity_type="invitation",
        entity_id=invitation.pk,
    ).exists()


@pytest.mark.django_db
def test_project_form_saves_discord_webhook_url(client_logged_in, user):
    webhook = "https://discord.com/api/webhooks/1/abc"
    response = client_logged_in.post(
        reverse("projects:create"),
        {
            "name": "Webhook Project",
            "description": "",
            "abstract": "",
            "keywords": "",
            "status": ProjectStatus.PLANNING,
            "is_public": "on",
            "public_summary": "",
            "discord_webhook_url": webhook,
        },
    )
    assert response.status_code in (301, 302)
    project = Project.objects.get(name="Webhook Project")
    assert project.discord_webhook_url == webhook


@pytest.mark.django_db
def test_accept_invitation_adds_member(client, user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Inv2", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    invitation = create_invitation(
        project=project,
        email=other_user.email,
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by="alice",
    )
    code = invitation.invitation_code

    client.force_login(other_user)
    # GET should render the accept page.
    get_resp = client.get(
        reverse("projects:accept_or_decline", kwargs={"code": code})
    )
    assert get_resp.status_code == 200

    # POST accept.
    post_resp = client.post(
        reverse("projects:accept_or_decline", kwargs={"code": code}),
        {"action": "accept"},
    )
    assert post_resp.status_code in (301, 302)
    assert ProjectMember.objects.filter(project=project, users=other_user).exists()
    # Invitation should be consumed.
    assert not Invitation.objects.filter(pk=invitation.pk).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.INVITATION_ACCEPTED, actor=other_user, project=project
    ).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.MEMBER_ADDED, actor=other_user, project=project
    ).exists()


# ---------------------------------------------------------------------------
# Join requests
# ---------------------------------------------------------------------------


def _make_project(user, *, is_public=True):
    return create_project_with_owner(
        user=user,
        form_data={
            "name": "JR Project",
            "status": ProjectStatus.ACTIVE,
            "is_public": is_public,
        },
    )


@pytest.mark.django_db
def test_create_join_request_records_audit_and_blocks_duplicates(user, other_user):
    project = _make_project(user)
    jr = create_join_request(
        project=project,
        user=other_user,
        requested_privilege=MemberPrivileges.CONTRIBUTOR,
        message="Hi!",
    )
    assert jr.status == JoinRequestStatus.PENDING
    assert AuditLog.objects.filter(
        action=AuditAction.JOIN_REQUEST_CREATED,
        actor=other_user,
        project=project,
        entity_id=jr.pk,
    ).exists()

    with pytest.raises(JoinRequestError):
        create_join_request(
            project=project,
            user=other_user,
            requested_privilege=MemberPrivileges.CONTRIBUTOR,
        )


@pytest.mark.django_db
def test_create_join_request_blocked_for_existing_members(user):
    project = _make_project(user)
    # ``user`` is the PM owner — already a member.
    with pytest.raises(JoinRequestError):
        create_join_request(
            project=project,
            user=user,
            requested_privilege=MemberPrivileges.CONTRIBUTOR,
        )


@pytest.mark.django_db
def test_approve_join_request_adds_member_at_requested_privilege(user, other_user):
    project = _make_project(user)
    jr = create_join_request(
        project=project,
        user=other_user,
        requested_privilege=MemberPrivileges.PROJECT_MANAGER,
    )
    approve_join_request(join_request=jr, approver=user)
    jr.refresh_from_db()
    assert jr.status == JoinRequestStatus.APPROVED
    assert jr.decided_by == user

    assert ProjectMember.objects.filter(project=project, users=other_user).exists()
    assert ProjectPrivilege.objects.filter(
        project=project,
        user=other_user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.JOIN_REQUEST_APPROVED,
        actor=user,
        project=project,
    ).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.MEMBER_ADDED, actor=user, project=project
    ).exists()


@pytest.mark.django_db
def test_decline_join_request_marks_declined_no_membership(user, other_user):
    project = _make_project(user)
    jr = create_join_request(
        project=project,
        user=other_user,
        requested_privilege=MemberPrivileges.CONTRIBUTOR,
    )
    decline_join_request(join_request=jr, approver=user, reason="Not now")
    jr.refresh_from_db()
    assert jr.status == JoinRequestStatus.DECLINED
    assert jr.decision_note == "Not now"
    assert not ProjectPrivilege.objects.filter(
        project=project, user=other_user
    ).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.JOIN_REQUEST_DECLINED, project=project
    ).exists()


@pytest.mark.django_db
def test_cancel_join_request_only_requester(user, other_user, pm_user):
    project = _make_project(user)
    jr = create_join_request(
        project=project,
        user=other_user,
        requested_privilege=MemberPrivileges.CONTRIBUTOR,
    )
    with pytest.raises(JoinRequestError):
        cancel_join_request(join_request=jr, user=pm_user)

    cancel_join_request(join_request=jr, user=other_user)
    jr.refresh_from_db()
    assert jr.status == JoinRequestStatus.CANCELLED


@pytest.mark.django_db
def test_request_to_join_view_requires_login(client, user):
    project = _make_project(user)
    response = client.get(
        reverse("projects:request_to_join", kwargs={"pk": project.pk})
    )
    assert response.status_code in (301, 302)
    assert "/accounts/login" in response["Location"]


@pytest.mark.django_db
def test_request_to_join_post_creates_request(client, user, other_user):
    project = _make_project(user)
    client.force_login(other_user)
    response = client.post(
        reverse("projects:request_to_join", kwargs={"pk": project.pk}),
        {
            "requested_privilege": MemberPrivileges.PROJECT_MANAGER,
            "message": "I'd love to lead this.",
        },
    )
    assert response.status_code in (301, 302)
    jr = JoinRequest.objects.get(project=project, user=other_user)
    assert jr.requested_privilege == MemberPrivileges.PROJECT_MANAGER
    assert jr.status == JoinRequestStatus.PENDING


@pytest.mark.django_db
def test_request_to_join_redirects_when_already_member(client, user):
    project = _make_project(user)
    client.force_login(user)
    response = client.get(
        reverse("projects:request_to_join", kwargs={"pk": project.pk})
    )
    # PM owner is already a member -> redirected to detail.
    assert response.status_code in (301, 302)
    assert reverse("projects:detail", kwargs={"pk": project.pk}) in response["Location"]


@pytest.mark.django_db
def test_join_request_list_pm_only(client, user, other_user):
    project = _make_project(user)
    client.force_login(other_user)
    response = client.get(
        reverse("projects:join_requests", kwargs={"pk": project.pk})
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_approve_decline_endpoints_pm_only(client, user, other_user):
    project = _make_project(user)
    jr = create_join_request(
        project=project,
        user=other_user,
        requested_privilege=MemberPrivileges.CONTRIBUTOR,
    )
    client.force_login(other_user)
    approve_url = reverse(
        "projects:approve_join_request",
        kwargs={"pk": project.pk, "join_request_pk": jr.pk},
    )
    response = client.post(approve_url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_pm_can_approve_via_view(client, user, other_user):
    project = _make_project(user)
    jr = create_join_request(
        project=project,
        user=other_user,
        requested_privilege=MemberPrivileges.CONTRIBUTOR,
    )
    client.force_login(user)
    approve_url = reverse(
        "projects:approve_join_request",
        kwargs={"pk": project.pk, "join_request_pk": jr.pk},
    )
    response = client.post(approve_url)
    assert response.status_code in (301, 302)
    jr.refresh_from_db()
    assert jr.status == JoinRequestStatus.APPROVED
    assert ProjectPrivilege.objects.filter(
        project=project, user=other_user
    ).exists()


@pytest.mark.django_db
def test_invitation_form_accepts_existing_user(client_logged_in, user, other_user):
    project = _make_project(user)
    response = client_logged_in.post(
        reverse("projects:invite", kwargs={"pk": project.pk}),
        {
            "invited_user": other_user.pk,
            "email": "",
            "privilege": MemberPrivileges.CONTRIBUTOR,
        },
    )
    assert response.status_code in (301, 302)
    invitation = Invitation.objects.get(project=project)
    assert invitation.invited_user_id == other_user.pk
    assert invitation.email == other_user.email.lower()


@pytest.mark.django_db
def test_invitation_form_rejects_existing_member(client_logged_in, user, other_user):
    project = _make_project(user)
    # Make other_user already a member.
    member = ProjectMember.objects.create(project=project)
    member.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    response = client_logged_in.post(
        reverse("projects:invite", kwargs={"pk": project.pk}),
        {
            "invited_user": other_user.pk,
            "email": "",
            "privilege": MemberPrivileges.CONTRIBUTOR,
        },
    )
    # invited_user queryset excludes existing members -> form refuses the choice.
    assert response.status_code == 200
    assert not Invitation.objects.filter(project=project).exists()


@pytest.mark.django_db
def test_detail_shows_request_to_join_button_for_non_members(client, user, other_user):
    project = _make_project(user)
    client.force_login(other_user)
    response = client.get(reverse("projects:detail", kwargs={"pk": project.pk}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Request to join as member" in content
    assert "Request to join as lead" in content


# ---------------------------------------------------------------------------
# Last-PM invariant
# ---------------------------------------------------------------------------


def _add_pm(project, target_user):
    """Helper: make ``target_user`` a PROJECT_MANAGER on ``project``."""
    member = ProjectMember.objects.create(project=project)
    member.users.add(target_user)
    ProjectPrivilege.objects.create(
        project=project,
        user=target_user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    )
    return member


@pytest.mark.django_db
def test_is_last_project_manager_true_when_solo(user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Solo PM", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    assert is_last_project_manager(project=project, user=user) is True


@pytest.mark.django_db
def test_is_last_project_manager_false_when_another_pm_exists(user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Two PMs", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    _add_pm(project, other_user)
    assert is_last_project_manager(project=project, user=user) is False


@pytest.mark.django_db
def test_assert_pm_self_action_blocks_solo_self_demote(user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Solo", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    with pytest.raises(LastProjectManagerError):
        assert_pm_self_action_allowed(
            project=project,
            actor=user,
            target_user=user,
            new_privilege=MemberPrivileges.CONTRIBUTOR,
        )


@pytest.mark.django_db
def test_assert_pm_self_action_blocks_solo_self_removal(user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Solo", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    with pytest.raises(LastProjectManagerError):
        assert_pm_self_action_allowed(
            project=project,
            actor=user,
            target_user=user,
            new_privilege=None,
        )


@pytest.mark.django_db
def test_assert_pm_self_action_allows_acting_on_others(user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Solo", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    _add_pm(project, other_user)
    # Solo PM demoting another PM is fine — actor remains PM.
    assert_pm_self_action_allowed(
        project=project,
        actor=user,
        target_user=other_user,
        new_privilege=MemberPrivileges.CONTRIBUTOR,
    )


@pytest.mark.django_db
def test_assert_pm_self_action_allows_self_when_coexisting_pm(user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Two", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    _add_pm(project, other_user)
    # Co-PM exists — self-demotion is allowed.
    assert_pm_self_action_allowed(
        project=project,
        actor=user,
        target_user=user,
        new_privilege=MemberPrivileges.CONTRIBUTOR,
    )


@pytest.mark.django_db
def test_last_pm_cannot_self_demote_via_view(client, user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.get(project=project, users=user)
    client.force_login(user)
    response = client.post(
        reverse(
            "projects:update_privilege",
            kwargs={"pk": project.pk, "member_pk": member.pk},
        ),
        {"privilege": MemberPrivileges.CONTRIBUTOR},
    )
    assert response.status_code in (301, 302)
    # Privilege unchanged.
    assert ProjectPrivilege.objects.filter(
        project=project, user=user, privilege=MemberPrivileges.PROJECT_MANAGER
    ).exists()
    assert not ProjectPrivilege.objects.filter(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    ).exists()


@pytest.mark.django_db
def test_last_pm_cannot_self_demote_via_htmx_returns_400(client, user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.get(project=project, users=user)
    client.force_login(user)
    response = client.post(
        reverse(
            "projects:update_privilege",
            kwargs={"pk": project.pk, "member_pk": member.pk},
        ),
        {"privilege": MemberPrivileges.CONTRIBUTOR},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_last_pm_cannot_self_remove_via_view(client, user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.get(project=project, users=user)
    client.force_login(user)
    response = client.post(
        reverse(
            "projects:remove_member",
            kwargs={"pk": project.pk, "member_pk": member.pk},
        )
    )
    assert response.status_code in (301, 302)
    member.refresh_from_db()
    assert member.deleted is False
    assert ProjectPrivilege.objects.filter(
        project=project, user=user, privilege=MemberPrivileges.PROJECT_MANAGER
    ).exists()


@pytest.mark.django_db
def test_pm_can_self_demote_when_another_pm_exists(client, user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    _add_pm(project, other_user)
    member = ProjectMember.objects.get(project=project, users=user)
    client.force_login(user)
    response = client.post(
        reverse(
            "projects:update_privilege",
            kwargs={"pk": project.pk, "member_pk": member.pk},
        ),
        {"privilege": MemberPrivileges.CONTRIBUTOR},
    )
    assert response.status_code == 200
    assert ProjectPrivilege.objects.filter(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    ).exists()
    assert not ProjectPrivilege.objects.filter(
        project=project, user=user, privilege=MemberPrivileges.PROJECT_MANAGER
    ).exists()


@pytest.mark.django_db
def test_pm_can_self_remove_when_another_pm_exists(client, user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    _add_pm(project, other_user)
    member = ProjectMember.objects.get(project=project, users=user)
    client.force_login(user)
    response = client.post(
        reverse(
            "projects:remove_member",
            kwargs={"pk": project.pk, "member_pk": member.pk},
        )
    )
    assert response.status_code in (301, 302)
    member.refresh_from_db()
    assert member.deleted is True
    assert not ProjectPrivilege.objects.filter(
        project=project, user=user
    ).exists()


@pytest.mark.django_db
def test_pm_can_demote_another_pm(client, user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    other_member = _add_pm(project, other_user)
    client.force_login(user)
    response = client.post(
        reverse(
            "projects:update_privilege",
            kwargs={"pk": project.pk, "member_pk": other_member.pk},
        ),
        {"privilege": MemberPrivileges.CONTRIBUTOR},
    )
    assert response.status_code == 200
    assert ProjectPrivilege.objects.filter(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    ).exists()


@pytest.mark.django_db
def test_members_page_disables_self_controls_for_last_pm(client, user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    client.force_login(user)
    response = client.get(reverse("projects:members", kwargs={"pk": project.pk}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "A project must always have at least one Project Manager." in content
