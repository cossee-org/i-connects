"""Tests for the GitHub-style project section tabs in the top bar.

The strip is rendered by ``base.html`` on project-scoped pages for project
members. It exposes three tabs: Tasks (board), Contributors (members) and
About (project detail). Opening a project from a member surface lands on the
Tasks board.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.enums import ProjectStatus
from apps.projects.services import create_project_with_owner


def _make_project(owner):
    return create_project_with_owner(
        user=owner,
        form_data={
            "name": "Tab Project",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )


@pytest.mark.django_db
def test_member_sees_project_tabs_on_board(client, user):
    project = _make_project(user)
    client.force_login(user)

    response = client.get(reverse("tasks:board", kwargs={"project_pk": project.pk}))
    content = response.content.decode()

    assert response.status_code == 200
    # The strip and all three tabs are present.
    assert 'aria-label="Project sections"' in content
    assert reverse("tasks:board", kwargs={"project_pk": project.pk}) in content
    assert reverse("projects:members", kwargs={"pk": project.pk}) in content
    assert reverse("projects:detail", kwargs={"pk": project.pk}) in content
    # On the board, the Tasks tab is the active one.
    assert "project-tab active" in content


@pytest.mark.django_db
def test_member_sees_project_tabs_on_about(client, user):
    project = _make_project(user)
    client.force_login(user)

    response = client.get(reverse("projects:detail", kwargs={"pk": project.pk}))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'aria-label="Project sections"' in content
    assert "project-tab active" in content


@pytest.mark.django_db
def test_non_member_does_not_see_tabs_on_public_about(client, user, other_user):
    """A logged-in non-member viewing a public project's About page gets no strip
    (the Tasks/Contributors pages would 403 for them)."""
    project = _make_project(user)
    client.force_login(other_user)

    response = client.get(reverse("projects:detail", kwargs={"pk": project.pk}))

    assert response.status_code == 200
    assert 'aria-label="Project sections"' not in response.content.decode()


@pytest.mark.django_db
def test_my_projects_cards_link_to_board(client, user):
    """Opening a project from My Projects lands on the Tasks board."""
    project = _make_project(user)
    client.force_login(user)

    response = client.get(reverse("projects:my_list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert reverse("tasks:board", kwargs={"project_pk": project.pk}) in content
