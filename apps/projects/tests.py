"""Smoke tests for the projects app."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import MemberPrivileges, ProjectStatus
from apps.projects.models import (
    Invitation,
    Project,
    ProjectMember,
    ProjectPrivilege,
)
from apps.projects.services import create_invitation, create_project_with_owner


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
