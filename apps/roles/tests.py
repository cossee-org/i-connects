"""Smoke tests for the roles app."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import ProjectStatus
from apps.projects.services import create_project_with_owner
from apps.roles.models import Role


@pytest.mark.django_db
def test_role_create_logs_audit(client, user):
    project = create_project_with_owner(
        user=user,
        form_data={
            "name": "RolesProj",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )
    client.force_login(user)
    response = client.post(
        reverse("roles:create", kwargs={"project_pk": project.pk}),
        {"name": "Reviewer", "description": "", "taxonomy": ""},
    )
    assert response.status_code == 200
    role = Role.objects.get(project=project, name="Reviewer")
    assert AuditLog.objects.filter(
        action=AuditAction.ROLE_CREATED,
        actor=user,
        project=project,
        entity_type="role",
        entity_id=role.pk,
    ).exists()


@pytest.mark.django_db
def test_role_delete_logs_audit(client, user):
    project = create_project_with_owner(
        user=user,
        form_data={
            "name": "RolesProj2",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )
    role = Role.objects.create(user=user, project=project, name="Editor")
    client.force_login(user)
    response = client.post(
        reverse(
            "roles:delete",
            kwargs={"project_pk": project.pk, "pk": role.pk},
        )
    )
    assert response.status_code in (200, 301, 302)
    assert not Role.objects.filter(pk=role.pk).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.ROLE_DELETED,
        actor=user,
        project=project,
        entity_type="role",
        entity_id=role.pk,
    ).exists()
