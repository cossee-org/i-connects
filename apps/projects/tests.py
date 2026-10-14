"""Smoke tests for the projects app."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import JoinRequestStatus, MemberPrivileges, ProjectStatus, TaskStatus
from apps.projects.models import (
    ContributionTracking,
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
    resend_invitation,
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
def test_create_project_can_select_credit_tracking(client_logged_in, user):
    response = client_logged_in.post(
        reverse("projects:create"),
        {
            "name": "CRediT Project",
            "description": "A project",
            "status": ProjectStatus.PLANNING,
            "is_public": "on",
            "contribution_tracking": ContributionTracking.CREDIT,
        },
    )
    assert response.status_code in (301, 302)
    project = Project.objects.get(name="CRediT Project")
    assert project.contribution_tracking == ContributionTracking.CREDIT


@pytest.mark.django_db
def test_create_project_without_tracking_defaults_to_none(client_logged_in, user):
    response = client_logged_in.post(
        reverse("projects:create"),
        {
            "name": "Untracked Project",
            "description": "A project",
            "status": ProjectStatus.PLANNING,
            "is_public": "on",
        },
    )
    assert response.status_code in (301, 302)
    project = Project.objects.get(name="Untracked Project")
    assert project.contribution_tracking == ContributionTracking.NONE


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
def test_resend_invitation_regenerates_code_and_resends_email(user):
    from django.core import mail

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Inv Resend", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    invitation = create_invitation(
        project=project,
        email="new@test.com",
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by="alice",
    )
    original_code = invitation.invitation_code
    mail.outbox.clear()

    resend_invitation(invitation=invitation, resender=user)
    invitation.refresh_from_db()

    assert invitation.invitation_code != original_code
    assert len(mail.outbox) == 1
    assert invitation.invitation_code in mail.outbox[0].body
    assert AuditLog.objects.filter(
        action=AuditAction.INVITATION_RESENT,
        entity_type="invitation",
        entity_id=invitation.pk,
    ).exists()


@pytest.mark.django_db
def test_resend_invitation_view_requires_project_manager(client, user, other_user):
    project = _make_project(user)
    invitation = create_invitation(
        project=project,
        email="new@test.com",
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by="alice",
    )
    # A contributor, not a PM, may not resend.
    member = ProjectMember.objects.create(project=project)
    member.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    client.force_login(other_user)
    response = client.post(
        reverse(
            "projects:resend_invitation",
            kwargs={"pk": project.pk, "invitation_pk": invitation.pk},
        )
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_resend_invitation_view_redirects_pm_to_invitation_list(client_logged_in, user):
    project = _make_project(user)
    invitation = create_invitation(
        project=project,
        email="new@test.com",
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by="alice",
    )
    response = client_logged_in.post(
        reverse(
            "projects:resend_invitation",
            kwargs={"pk": project.pk, "invitation_pk": invitation.pk},
        )
    )
    assert response.status_code in (301, 302)
    assert response["Location"] == reverse(
        "projects:project_invitations", kwargs={"pk": project.pk}
    )


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


@pytest.mark.django_db
def test_dashboard_projects_card_links_to_projects_module_and_drops_old_sections(client, user):
    create_project_with_owner(
        user=user,
        form_data={"name": "Sole Project", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    client.force_login(user)
    response = client.get(reverse("projects:dashboard"))
    content = response.content.decode()

    assert response.status_code == 200
    assert reverse("projects:my_list") in content
    # The standalone Projects/Open Tasks/Join Requests/Invitations sections
    # are gone -- their content now lives in the unified timeline instead.
    assert 'id="projects-heading"' not in content
    assert 'id="active-tasks-heading"' not in content
    assert 'id="join-requests-heading"' not in content
    assert 'id="invitations-heading"' not in content


# ---------------------------------------------------------------------------
# Dashboard: active-task and request lists
# ---------------------------------------------------------------------------


def _task(project, member, name, **kwargs):
    """Create a task on ``project``, ordered after the ones before it."""
    from apps.tasks.models import KanbanBoard, Task

    column = KanbanBoard.objects.filter(project=project).first()
    return Task.objects.create(
        project=project,
        created_by=member,
        container=column,
        container_task_order=Task.objects.filter(project=project).count(),
        name=name,
        **kwargs,
    )


def _active_task_rows(content):
    """The dashboard's active-task list, sliced out of the rendered page so
    an assertion about a row cannot be satisfied by the surrounding chrome."""
    start = content.index('<ul aria-label="Active tasks"')
    return content[start : content.index("</ul>", start)]


@pytest.mark.django_db
def test_dashboard_active_task_list_orders_by_deadline_and_caps(client, user):
    """The list is ordered by deadline (undated last either way) and shows
    at most ACTIVE_TASK_LIMIT rows, with a link out to the full Tasks
    page."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.projects.views import DashboardView

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Deadlines", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    now = timezone.now()

    overflow = DashboardView.ACTIVE_TASK_LIMIT + 1
    for i in range(overflow):
        task = _task(project, member, f"Dated task {i}", deadline=now + timedelta(days=i + 1))
        task.assigned_members.add(member)
    undated = _task(project, member, "Undated task")
    undated.assigned_members.add(member)

    client.force_login(user)
    response = client.get(reverse("projects:dashboard"), {"sort": "oldest"})
    content = response.content.decode()

    assert response.status_code == 200
    assert 'id="dashboard-open-work-heading"' in content
    # Oldest first, capped: the earliest deadlines are shown...
    for i in range(DashboardView.ACTIVE_TASK_LIMIT):
        assert f"Dated task {i}" in content
    # ...and everything past the cap is not, undated tasks included even
    # though they sort last only because of the nulls_last ordering.
    assert f"Dated task {overflow - 1}" not in content
    assert "Undated task" not in content
    # The count on the section, and the link out, cover the whole list.
    assert response.context["active_tasks_total"] == overflow + 1
    assert f"All {overflow + 1} tasks" in content
    assert reverse("tasks:my_list") in content


@pytest.mark.django_db
def test_dashboard_active_task_list_flags_review_work_and_skips_finished_tasks(client, user):
    """A task the user only reviews is listed and marked "To review"; tasks
    the user has already marked complete, and closed tasks, are not listed."""
    from apps.tasks.models import TaskLog

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Mixed", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()

    reviewing = _task(project, member, "Task to review")
    reviewing.reviewers.add(member)

    self_marked = _task(project, member, "Task I finished")
    self_marked.assigned_members.add(member)
    TaskLog.objects.create(task=self_marked, assigned_to=member, status=TaskStatus.COMPLETED)

    closed = _task(project, member, "Closed task", status=TaskStatus.COMPLETED)
    closed.assigned_members.add(member)

    client.force_login(user)
    response = client.get(reverse("projects:dashboard"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Task to review" in content
    assert "To review" in content
    assert "Task I finished" not in content
    assert "Closed task" not in content
    assert response.context["active_tasks_total"] == 1


@pytest.mark.django_db
def test_dashboard_active_task_rows_label_the_deadline_and_drop_assignees(client, user):
    """A deadline still to come reads "Due <date>"; an overdue one keeps
    the red "Overdue" wording it already had. Neither row names who the
    task is assigned to -- on this list that is almost always the reader."""
    from datetime import timedelta

    from django.utils import timezone

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Dated", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    now = timezone.now()

    upcoming = _task(project, member, "Upcoming task", deadline=now + timedelta(days=3))
    upcoming.assigned_members.add(member)
    late = _task(project, member, "Late task", deadline=now - timedelta(days=3))
    late.assigned_members.add(member)

    client.force_login(user)
    content = client.get(reverse("projects:dashboard")).content.decode()
    rows = _active_task_rows(content)

    assert f"Due {(now + timedelta(days=3)).strftime('%b %-d')}" in rows
    assert f"Overdue &middot; {(now - timedelta(days=3)).strftime('%b %-d')}" in rows
    # The overdue row keeps its red treatment.
    assert "text-error font-medium" in rows
    # Neither row names who the task is assigned to. Scoped to the list:
    # the navbar prints the reader's own name too.
    assert user.name not in rows

@pytest.mark.django_db
def test_dashboard_priority_task_row_marks_priority_with_the_flag_alone(client, user):
    """A high-priority task is marked once -- the red flag, carrying its
    wording in a hover tooltip and an aria-label -- rather than by a flag
    and a red border down the row."""
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Urgent", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    task = _task(project, member, "Urgent task", is_priority=True)
    task.assigned_members.add(member)

    client.force_login(user)
    rows = _active_task_rows(client.get(reverse("projects:dashboard")).content.decode())

    assert 'aria-label="High priority"' in rows
    assert 'data-tooltip="High priority"' in rows
    # Left-hung, so the scroll box the list sits in cannot clip it.
    assert "hover-tooltip hover-tooltip-left" in rows
    assert "border-l-error" not in rows


@pytest.mark.django_db
def test_dashboard_active_task_list_shows_an_empty_state(client, user):
    create_project_with_owner(
        user=user,
        form_data={"name": "Quiet", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    client.force_login(user)
    response = client.get(reverse("projects:dashboard"))
    content = response.content.decode()

    assert response.status_code == 200
    # Both halves of the section are empty, so it collapses to one panel
    # rather than stacking a "no tasks" and a "no requests" one.
    assert "Nothing waiting on you" in content
    assert "No tasks right now" not in content
    assert "No requests waiting" not in content
    # Nothing is hidden, so there is no "view the rest" link to render.
    assert "All 0 tasks" not in content


@pytest.mark.django_db
def test_dashboard_request_list_merges_join_requests_and_invitations(client, user, other_user):
    """Join requests on a project the user manages and invitations addressed
    to the user land in one list, each with a link to act on it."""
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Managed", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    join_request = create_join_request(
        project=project, user=other_user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )
    invited_to = create_project_with_owner(
        user=other_user,
        form_data={"name": "Invited", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    invitation = create_invitation(
        project=invited_to,
        email=user.email,
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by=other_user,
    )

    client.force_login(user)
    response = client.get(reverse("projects:dashboard"))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'id="dashboard-open-work-heading"' in content
    assert response.context["incoming_request_count"] == 2
    assert other_user.name in content
    assert "Invited" in content
    assert (
        reverse(
            "projects:review_join_request",
            kwargs={"pk": project.pk, "join_request_pk": join_request.pk},
        )
        in content
    )
    assert (
        reverse("projects:accept_or_decline", kwargs={"code": invitation.invitation_code})
        in content
    )


@pytest.mark.django_db
def test_dashboard_request_list_ignores_requests_the_user_cannot_act_on(client, user, other_user):
    """A join request on someone else's project, and a resolved one on the
    user's own project, are both absent -- neither is waiting on the user."""
    mine = create_project_with_owner(
        user=user,
        form_data={"name": "Mine", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    theirs = create_project_with_owner(
        user=other_user,
        form_data={"name": "Theirs", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    resolved = create_join_request(
        project=mine, user=other_user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )
    resolved.status = JoinRequestStatus.DECLINED
    resolved.save(update_fields=["status"])
    create_join_request(
        project=theirs, user=user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )
    # An active task keeps the section from collapsing to its combined
    # empty state, so the requests column's own empty panel is what shows.
    member = ProjectMember.objects.filter(project=mine, users=user).first()
    _task(mine, member, "Something to do").assigned_members.add(member)

    client.force_login(user)
    response = client.get(reverse("projects:dashboard"))
    content = response.content.decode()

    assert response.status_code == 200
    assert response.context["incoming_request_count"] == 0
    assert "No requests waiting" in content
    assert "All 0 requests" not in content


@pytest.mark.django_db
def test_dashboard_type_filter_shows_one_group_at_a_time(client, user, other_user):
    """"Tasks only" drops the request rows and "Requests only" drops the
    task rows, and the section's count follows what is left showing."""
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Filtered", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    _task(project, member, "A task of mine").assigned_members.add(member)
    create_join_request(
        project=project, user=other_user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )

    client.force_login(user)
    url = reverse("projects:dashboard")

    both = client.get(url).content.decode()
    assert "A task of mine" in both
    assert other_user.name in both

    tasks_only = client.get(url, {"type": "tasks"})
    content = tasks_only.content.decode()
    assert "A task of mine" in content
    assert other_user.name not in content
    # Not the "no requests" line either -- they were filtered out, not absent.
    assert "No requests waiting" not in content
    assert tasks_only.context["active_tasks_match_count"] == 1
    assert tasks_only.context["incoming_request_match_count"] == 0

    requests_only = client.get(url, {"type": "requests"})
    content = requests_only.content.decode()
    assert other_user.name in content
    assert "A task of mine" not in content
    assert "No tasks right now" not in content
    assert requests_only.context["incoming_request_match_count"] == 1
    assert requests_only.context["active_tasks_match_count"] == 0


@pytest.mark.django_db
def test_dashboard_sort_runs_tasks_by_deadline_in_both_directions(client, user):
    """Both sorts key off the deadline the row shows; a task with no
    deadline sorts last either way rather than leading the oldest list."""
    from datetime import timedelta

    from django.utils import timezone

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Sorted", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    now = timezone.now()
    _task(project, member, "Soon", deadline=now + timedelta(days=1)).assigned_members.add(member)
    _task(project, member, "Later", deadline=now + timedelta(days=9)).assigned_members.add(member)
    _task(project, member, "Undated").assigned_members.add(member)

    client.force_login(user)
    url = reverse("projects:dashboard")

    # Oldest first is the default: soonest due leads, undated trails.
    oldest = client.get(url)
    assert [t.name for t in oldest.context["active_tasks"]] == ["Soon", "Later", "Undated"]
    assert oldest.context["current_sort"] == "oldest"

    newest = client.get(url, {"sort": "newest"})
    assert [t.name for t in newest.context["active_tasks"]] == ["Later", "Soon", "Undated"]
    assert newest.context["current_sort"] == "newest"


@pytest.mark.django_db
def test_dashboard_sort_runs_requests_by_age_in_both_directions(client, user, other_user):
    """A request has no deadline, so its date is when it arrived."""
    first = create_project_with_owner(
        user=user,
        form_data={"name": "First Asked", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    second = create_project_with_owner(
        user=user,
        form_data={"name": "Second Asked", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    create_join_request(
        project=first, user=other_user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )
    create_join_request(
        project=second, user=other_user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )

    client.force_login(user)
    url = reverse("projects:dashboard")

    oldest = client.get(url)
    assert [
        item["obj"].project.name for item in oldest.context["incoming_requests"]
    ] == ["First Asked", "Second Asked"]

    newest = client.get(url, {"sort": "newest"})
    assert [
        item["obj"].project.name for item in newest.context["incoming_requests"]
    ] == ["Second Asked", "First Asked"]


@pytest.mark.django_db
def test_dashboard_ignores_unknown_filter_and_sort_values(client, user):
    """A hand-edited query string falls back to the defaults rather than
    erroring or dropping the list."""
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Defaults", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    _task(project, member, "Still listed").assigned_members.add(member)

    client.force_login(user)
    response = client.get(
        reverse("projects:dashboard"), {"type": "bogus", "sort": "whatever"}
    )

    assert response.status_code == 200
    assert response.context["current_type"] == ""
    assert response.context["current_sort"] == "oldest"
    assert "Still listed" in response.content.decode()


@pytest.mark.django_db
def test_dashboard_overdue_type_keeps_only_tasks_past_their_deadline(client, user):
    from datetime import timedelta

    from django.utils import timezone

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Due", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    now = timezone.now()
    _task(project, member, "Late task", deadline=now - timedelta(days=2)).assigned_members.add(member)
    _task(project, member, "Future task", deadline=now + timedelta(days=2)).assigned_members.add(member)
    _task(project, member, "Undated task").assigned_members.add(member)

    client.force_login(user)
    response = client.get(reverse("projects:dashboard"), {"type": "overdue"})
    content = response.content.decode()

    assert response.status_code == 200
    assert "Late task" in content
    assert "Future task" not in content
    assert "Undated task" not in content
    # The list is narrowed to one row; the Tasks stat tile still counts all.
    assert response.context["active_tasks_match_count"] == 1
    assert response.context["active_tasks_total"] == 3


@pytest.mark.django_db
def test_dashboard_review_type_keeps_only_tasks_the_user_reviews(client, user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Reviewed", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    _task(project, member, "Mine to do").assigned_members.add(member)
    _task(project, member, "Mine to review").reviewers.add(member)

    client.force_login(user)
    response = client.get(reverse("projects:dashboard"), {"type": "review"})
    content = response.content.decode()

    assert response.status_code == 200
    assert "Mine to review" in content
    assert "Mine to do" not in content
    assert response.context["active_tasks_match_count"] == 1


@pytest.mark.django_db
def test_dashboard_splits_requests_into_join_requests_and_invitations(client, user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Managed", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    create_join_request(
        project=project, user=other_user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )
    invited_to = create_project_with_owner(
        user=other_user,
        form_data={"name": "Invited Here", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    create_invitation(
        project=invited_to,
        email=user.email,
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by=other_user,
    )

    client.force_login(user)
    url = reverse("projects:dashboard")

    joins = client.get(url, {"type": "join_requests"})
    content = joins.content.decode()
    assert other_user.name in content
    assert "Invited Here" not in content
    assert joins.context["incoming_request_match_count"] == 1

    invites = client.get(url, {"type": "invitations"})
    content = invites.content.decode()
    assert "Invited Here" in content
    assert f"{other_user.name} requested to join" not in content
    assert invites.context["incoming_request_match_count"] == 1


@pytest.mark.django_db
def test_dashboard_link_and_empty_line_name_the_narrowed_set(client, user):
    """A filtered group says what it was filtered to, so "All N ..." never
    promises a count of something the user is not looking at."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.projects.views import DashboardView

    project = create_project_with_owner(
        user=user,
        form_data={"name": "Named", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    now = timezone.now()
    overflow = DashboardView.ACTIVE_TASK_LIMIT + 1
    for i in range(overflow):
        _task(
            project, member, f"Late task {i}", deadline=now - timedelta(days=i + 1)
        ).assigned_members.add(member)

    client.force_login(user)
    url = reverse("projects:dashboard")

    overdue = client.get(url, {"type": "overdue"}).content.decode()
    assert f"All {overflow} overdue tasks" in overdue

    # Nothing is overdue for a user whose only task is undated.
    _task(project, member, "Someday").assigned_members.add(member)
    review = client.get(url, {"type": "review"}).content.decode()
    assert "No tasks to review right now" in review

    invitations = client.get(url, {"type": "invitations"}).content.decode()
    assert "No invitations waiting on you right now" in invitations


@pytest.mark.django_db
def test_dashboard_request_rows_pill_lead_requests_only(client, user, other_user):
    """"Member" is what nearly every request asks for, so only the
    exception -- a request to lead the project -- carries a pill."""
    project = create_project_with_owner(
        user=user,
        form_data={"name": "Pilled", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    create_join_request(
        project=project, user=other_user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )

    client.force_login(user)
    content = client.get(reverse("projects:dashboard")).content.decode()
    assert other_user.name in content
    assert "Member" not in content

    lead_asker = get_user_model().objects.create_user(
        email="lead@test.com", password="password123", name="Lead Asker"
    )
    create_join_request(
        project=project, user=lead_asker, requested_privilege=MemberPrivileges.PROJECT_MANAGER
    )
    content = client.get(reverse("projects:dashboard")).content.decode()
    assert "Lead" in content
    assert "Member" not in content


# ---------------------------------------------------------------------------
# Discover projects: the projects you are already in
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_discover_marks_the_projects_you_belong_to(client, user, other_user):
    mine = create_project_with_owner(
        user=user,
        form_data={"name": "Mine", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    joined = create_project_with_owner(
        user=other_user,
        form_data={"name": "Joined", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    ProjectPrivilege.objects.create(
        project=joined, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    theirs = create_project_with_owner(
        user=other_user,
        form_data={"name": "Theirs", "status": ProjectStatus.ACTIVE, "is_public": True},
    )

    client.force_login(user)
    rows = {
        row["project"].name: row["my_privilege"]
        for row in client.get(reverse("projects:list")).context["project_rows"]
    }
    assert rows["Mine"] == MemberPrivileges.PROJECT_MANAGER
    assert rows["Joined"] == MemberPrivileges.CONTRIBUTOR
    # Nothing on a project you have no standing in.
    assert rows["Theirs"] is None


@pytest.mark.django_db
def test_discover_shows_the_membership_pill(client, user, other_user):
    create_project_with_owner(
        user=user,
        form_data={"name": "Mine", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    create_project_with_owner(
        user=other_user,
        form_data={"name": "Theirs", "status": ProjectStatus.ACTIVE, "is_public": True},
    )

    client.force_login(user)
    body = client.get(reverse("projects:list")).content.decode()
    # "Lead" is the label for the privilege that owning a project grants.
    assert "Lead" in body


@pytest.mark.django_db
def test_discover_marks_nothing_for_an_anonymous_visitor(client, user):
    """The page is open to visitors, who belong to nothing."""
    create_project_with_owner(
        user=user,
        form_data={"name": "Mine", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    response = client.get(reverse("projects:list"))
    assert response.status_code == 200
    assert [row["my_privilege"] for row in response.context["project_rows"]] == [None]


# ---------------------------------------------------------------------------
# Project dashboard: wiki card
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_project_dashboard_hides_wiki_from_non_members(client, user, other_user):
    """The wiki is members-only, and so is its card on a public project."""
    from apps.wiki.services import create_page

    project = _make_project(user)
    create_page(project=project, actor=user, title="Internal notes")
    client.force_login(other_user)
    content = client.get(
        reverse("projects:detail", kwargs={"pk": project.pk})
    ).content.decode()
    assert "Internal notes" not in content
    assert "Wiki pages" not in content


# ---------------------------------------------------------------------------
# Contributors sub-pages: laid out like a Wiki sub-page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name, heading",
    [
        ("projects:join_requests", "Pending join requests"),
        ("projects:project_invitations", "Pending invitation requests"),
    ],
)
def test_contributor_subpages_look_like_wiki_subpages(client, user, url_name, heading):
    project = _make_project(user)
    client.force_login(user)
    content = client.get(reverse(url_name, kwargs={"pk": project.pk})).content.decode()

    # The project's own header and tab strip, with Contributors as the tab.
    assert f">{project.name}</h1>" in content
    strip = content[content.index('aria-label="Project sections"'):]
    strip = strip[: strip.index("</nav>")]
    members_url = reverse("projects:members", kwargs={"pk": project.pk})
    assert f'href="{members_url}"\n       class="project-tab active"' in strip
    # Back to the tab first, then the page's own heading.
    back = f'href="{members_url}"\n     class="btn btn-sm btn-ghost text-brand-navy">&larr; All contributors</a>'
    assert back in content
    assert content.index(back) < content.index(f">{heading}</h2>")


# ---------------------------------------------------------------------------
# Project Overview: what the tab reports
# ---------------------------------------------------------------------------


def _summary_project(user, other_user):
    """A project with one task in each state the summary cards report."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.projects.models import ContributionTracking as CT
    from apps.tasks.models import KanbanBoard, Milestone, Task
    from apps.tasks.services import close_task, mark_own_done

    project = _make_project(user)
    project.contribution_tracking = CT.CREDIT
    project.save()
    lead = ProjectMember.objects.filter(project=project, users=user).first()
    helper = ProjectMember.objects.create(project=project)
    helper.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    col = KanbanBoard.objects.create(project=project, name="Doing", container_order=0)

    def task(name, **kw):
        t = Task.objects.create(
            project=project, created_by=lead, container=col,
            container_task_order=Task.objects.count(), name=name, **kw,
        )
        t.assigned_members.add(helper)
        return t

    now = timezone.now()
    done = task("Done one")
    mark_own_done(task=done, member=helper)
    close_task(task=done, closed_by=lead)                       # completed
    waiting = task("Waiting one")
    mark_own_done(task=waiting, member=helper)                  # ready for a lead
    task("Late one", deadline=now - timedelta(days=2))          # overdue
    task("Soon one", deadline=now + timedelta(days=3))          # coming up
    Milestone.objects.create(project=project, name="Ship it", end_date=now + timedelta(days=1))
    finished_late = task("Finished late", deadline=now - timedelta(days=5))
    mark_own_done(task=finished_late, member=helper)
    close_task(task=finished_late, closed_by=lead)              # not overdue
    return project


@pytest.mark.django_db
def test_summary_on_an_empty_project(client, user):
    project = _make_project(user)
    client.force_login(user)
    response = client.get(reverse("projects:detail", kwargs={"pk": project.pk}))
    body = response.content.decode()
    assert "Nothing has happened here yet." in body


@pytest.mark.django_db
def test_summary_reports_activity(client, user, other_user):
    project = _summary_project(user, other_user)

    client.force_login(user)
    response = client.get(reverse("projects:detail", kwargs={"pk": project.pk}))
    summary = response.context["summary"]

    # A task finished after its deadline is not overdue.
    assert response.context["overdue_count"] == 1

    # Recent activity covers completions and done marks, newest first.
    verbs = [e["verb"] for e in summary["activity"]]
    assert "completed" in verbs and "marked their part done on" in verbs
    whens = [e["when"] for e in summary["activity"]]
    assert whens == sorted(whens, reverse=True)
    assert all(e["ago"] == "just now" for e in summary["activity"])

    body = response.content.decode()
    # The Progress, Needs attention and Coming up cards are gone.
    for heading in ("proj-progress-heading", "proj-attention-heading", "proj-upcoming-heading"):
        assert f'id="{heading}"' not in body


@pytest.mark.django_db
def test_summary_activity_lists_reviews_and_a_task_its_reviews_finished(client, user, other_user):
    from apps.tasks.models import KanbanBoard, Task
    from apps.tasks.services import close_task, mark_own_done, mark_reviewed

    project = _make_project(user)
    lead = ProjectMember.objects.filter(project=project, users=user).first()  # Alice
    bob = ProjectMember.objects.create(project=project)
    bob.users.add(other_user)
    carol_user = get_user_model().objects.create_user(
        email="carol@example.com", password="pw-123456789", name="Carol"
    )
    carol = ProjectMember.objects.create(project=project)
    carol.users.add(carol_user)
    col = KanbanBoard.objects.create(project=project, name="Doing", container_order=0)

    reviewed = Task.objects.create(
        project=project, created_by=lead, container=col,
        container_task_order=0, name="Reviewed one",
    )
    reviewed.assigned_members.add(bob, carol)
    reviewed.reviewers.add(lead)
    bob_log = mark_own_done(task=reviewed, member=bob)
    carol_log = mark_own_done(task=reviewed, member=carol)

    by_hand = Task.objects.create(
        project=project, created_by=lead, container=col,
        container_task_order=1, name="Closed by hand",
    )
    close_task(task=by_hand, closed_by=lead)

    url = reverse("projects:detail", kwargs={"pk": project.pk})
    client.force_login(user)

    # One of two parts reviewed: a review line, and the task is still open.
    mark_reviewed(task_log=bob_log, reviewer=lead)
    activity = client.get(url).context["summary"]["activity"]
    assert ("Alice", "reviewed Bob's part on", "Reviewed one") in [
        (e["who"], e["verb"], e["task"].name) for e in activity if e["kind"] == "task"
    ]
    assert not any(e.get("after") for e in activity)

    # The last sign-off finishes the task: its own line, above the review,
    # and no "Alice completed" as though a lead had closed it by hand.
    mark_reviewed(task_log=carol_log, reviewer=lead)
    response = client.get(url)
    lines = [
        (e["who"], e["verb"], e["task"].name, e.get("after", ""))
        for e in response.context["summary"]["activity"] if e["kind"] == "task"
    ]
    assert lines[0] == ("", "", "Reviewed one", "was marked done: every part reviewed")
    assert lines[1] == ("Alice", "reviewed Carol's part on", "Reviewed one", "")
    assert ("Alice", "completed", "Reviewed one", "") not in lines
    # A task a lead closed by hand still reads as theirs.
    assert ("Alice", "completed", "Closed by hand", "") in lines

    body = response.content.decode()
    assert "reviewed Bob&#x27;s part on" in body
    assert "was marked done: every part reviewed" in body


@pytest.mark.django_db
def test_summary_number_cards_open_their_tabs(client, user):
    project = _make_project(user)
    client.force_login(user)
    body = client.get(reverse("projects:detail", kwargs={"pk": project.pk})).content.decode()
    board = reverse("tasks:board", kwargs={"project_pk": project.pk})
    for url in (
        board,                                  # Tasks
        f"{board}?overdue=1",                   # Overdue: the board, overdue only
        reverse("projects:join_requests", kwargs={"pk": project.pk}),
        reverse("projects:members", kwargs={"pk": project.pk}),
        reverse("wiki:list", kwargs={"project_pk": project.pk}),
    ):
        assert f'<a href="{url}"\n   class="brand-card p-4' in body, url
    # The wiki's number is labelled "Wiki", not "Wiki pages".
    assert ">Wiki pages</span>" not in body
    assert ">Wiki</span>" in body


@pytest.mark.django_db
def test_overview_is_one_full_width_column(client, user):
    """About, then the Wiki, then Recent activity, each the full width."""
    project = _make_project(user)
    client.force_login(user)
    body = client.get(reverse("projects:detail", kwargs={"pk": project.pk})).content.decode()
    assert "proj-columns" not in body
    assert (
        body.index('id="proj-about-heading"')
        < body.index('id="proj-wiki-heading"')
        < body.index('id="proj-activity-heading"')
    )


@pytest.mark.django_db
def test_summary_is_a_report_not_a_workbench(client, user):
    """Adding and editing live on the tabs; the summary only reports."""
    project = _make_project(user)
    client.force_login(user)
    body = client.get(reverse("projects:detail", kwargs={"pk": project.pk})).content.decode()
    assert reverse("tasks:create", kwargs={"project_pk": project.pk}) not in body
    assert reverse("wiki:create", kwargs={"project_pk": project.pk}) not in body
    assert "Delete task confirmation" not in body


@pytest.mark.django_db
def test_overview_wiki_card_lists_the_pages_put_on_the_overview(client, user):
    """Only the pages a lead put on the Overview, in one list: the locked ones
    first, by title and each with a lock, then the others, newest edit first."""
    from apps.wiki.services import create_page, update_page

    project = _make_project(user)
    on = {"show_on_overview": True}
    create_page(project=project, actor=user, title="Team agreement", locked=True, **on)
    create_page(project=project, actor=user, title="Data protocol", locked=True, **on)
    notes = create_page(project=project, actor=user, title="Meeting notes", **on)
    create_page(project=project, actor=user, title="Glossary", **on)
    create_page(project=project, actor=user, title="Scratchpad")          # not on it
    create_page(project=project, actor=user, title="Old rules", locked=True)  # nor this
    update_page(page=notes, actor=user, title=notes.title, content_markdown="Updated.")

    client.force_login(user)
    response = client.get(reverse("projects:detail", kwargs={"pk": project.pk}))
    wiki = response.context["summary"]["wiki"]
    assert [page.title for page in wiki["pages"]] == [
        "Data protocol", "Team agreement", "Meeting notes", "Glossary",
    ]

    body = response.content.decode()
    card = body[body.index('id="proj-wiki-heading"'):]
    card = card[: card.index("</section>")]
    assert "Key pages" not in card and "Recently updated" not in card
    # One plain list, no rail.
    assert card.count("<ul") == 1 and "wiki-rail" not in card
    order = [card.index(f">{t}</a>") for t in ("Data protocol", "Team agreement", "Meeting notes", "Glossary")]
    assert order == sorted(order)
    assert card.count('aria-label="Locked page"') == 2
    assert "Scratchpad" not in card and "Old rules" not in card
    # No dates, no rules between rows; the wiki holds more, so "See more".
    assert "Oct" not in card and "divide-y" not in card
    assert "See more" in card


@pytest.mark.django_db
def test_overview_wiki_card_lists_every_page_put_on_the_overview(client, user):
    from apps.wiki.services import create_page

    project = _make_project(user)
    create_page(
        project=project, actor=user, title="Team agreement", locked=True, show_on_overview=True
    )
    for title in ("One", "Two", "Three", "Four", "Five", "Six"):
        create_page(project=project, actor=user, title=title, show_on_overview=True)

    client.force_login(user)
    url = reverse("projects:detail", kwargs={"pk": project.pk})
    response = client.get(url)
    titles = [page.title for page in response.context["summary"]["wiki"]["pages"]]
    # No cap: the locked page leads, then every other one, newest edit first.
    assert titles == ["Team agreement", "Six", "Five", "Four", "Three", "Two", "One"]

    card = _wiki_card(response.content.decode())
    assert all(f">{t}</a>" in card for t in titles)
    # Every page in the wiki is already listed: nothing more to see.
    assert "See more" not in card

    # A page not on the Overview: "See more" opens the Wiki for it.
    create_page(project=project, actor=user, title="Scratchpad")
    card = _wiki_card(client.get(url).content.decode())
    wiki_url = reverse("wiki:list", kwargs={"project_pk": project.pk})
    assert "Scratchpad" not in card
    assert f'<a href="{wiki_url}"' in card and "See more" in card


def _wiki_card(body: str) -> str:
    """The Overview's Wiki card alone, out of the whole page."""
    card = body[body.index('id="proj-wiki-heading"'):]
    return card[: card.index("</section>")]


@pytest.mark.django_db
def test_overview_wiki_card_when_nothing_is_on_the_overview_or_written(client, user, other_user):
    from apps.wiki.services import create_page, update_page

    project = _make_project(user)
    url = reverse("projects:detail", kwargs={"pk": project.pk})
    client.force_login(user)
    body = client.get(url).content.decode()
    assert "No wiki pages yet." in body and "Go to the Wiki" in body

    # Pages, none on the Overview: the card says so, and a lead is told how
    # to put one there; a member is not.
    notes = create_page(project=project, actor=user, title="Notes")
    lead_body = client.get(url).content.decode()
    assert "No wiki pages are on the Overview yet." in lead_body
    assert "Show on Project Overview" in lead_body
    assert ">Notes</a>" not in _wiki_card(lead_body)

    member = ProjectMember.objects.create(project=project)
    member.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    client.force_login(other_user)
    member_body = client.get(url).content.decode()
    assert "No wiki pages are on the Overview yet." in member_body
    assert "Show on Project Overview" not in member_body

    # A lead puts it on the Overview: every member now sees it listed.
    update_page(
        page=notes, actor=user, title=notes.title, content_markdown="", show_on_overview=True
    )
    assert ">Notes</a>" in _wiki_card(client.get(url).content.decode())


@pytest.mark.django_db
def test_overview_wiki_follows_who_can_see_each_page(client, user, other_user):
    """A leads-only page is on a lead's Overview only — not on the card, in
    Recent activity or in the Wiki count for anyone else — and a visitor to
    a public project sees just the public pages put on the Overview."""
    from apps.wiki.models import WikiVisibility
    from apps.wiki.services import create_page

    project = _make_project(user)
    project.is_public = True
    project.save()
    on = {"show_on_overview": True}
    create_page(project=project, actor=user, title="Budget", visibility=WikiVisibility.LEADS, **on)
    create_page(project=project, actor=user, title="Protocol", **on)
    create_page(project=project, actor=user, title="About us", visibility=WikiVisibility.PUBLIC, **on)
    create_page(project=project, actor=user, title="Press kit", visibility=WikiVisibility.PUBLIC)
    url = reverse("projects:detail", kwargs={"pk": project.pk})

    client.force_login(user)
    lead = client.get(url)
    assert {p.title for p in lead.context["summary"]["wiki"]["pages"]} == {
        "Budget", "Protocol", "About us",
    }
    assert lead.context["wiki_page_count"] == 4

    member = ProjectMember.objects.create(project=project)
    member.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    client.force_login(other_user)
    contributor = client.get(url)
    assert {p.title for p in contributor.context["summary"]["wiki"]["pages"]} == {
        "Protocol", "About us",
    }
    assert contributor.context["wiki_page_count"] == 3
    assert "Budget" not in contributor.content.decode()  # not in Recent activity either

    # A visitor: not a member of this public project.
    member.delete()
    ProjectPrivilege.objects.filter(project=project, user=other_user).delete()
    visitor = client.get(url)
    assert [p.title for p in visitor.context["public_wiki_pages"]] == ["About us"]
    body = visitor.content.decode()
    assert 'id="proj-public-wiki-heading"' in body and ">About us</a>" in body
    assert "Protocol" not in body and "Budget" not in body and "Press kit" not in body
