"""Smoke tests for the notes app."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.enums import (
    MemberPrivileges,
    NoteVisibility,
    ProjectStatus,
)
from apps.notes.models import Note
from apps.notes.utils import render_markdown
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner


@pytest.mark.django_db
def test_private_note_only_visible_to_author(client, user, other_user):
    project = create_project_with_owner(
        user=user,
        form_data={
            "name": "NotesProj",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )
    # Both users are members; user is PM, other_user is a contributor.
    other_member = ProjectMember.objects.create(project=project)
    other_member.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    author_member = ProjectMember.objects.filter(project=project, users=user).first()

    Note.objects.create(
        project=project,
        author=author_member,
        title="Secret",
        content_markdown="shhh",
        visibility=NoteVisibility.PRIVATE,
    )

    # user (author) should see 1 note.
    client.force_login(user)
    resp_author = client.get(
        reverse("notes:list", kwargs={"project_pk": project.pk})
    )
    assert resp_author.status_code == 200
    assert len(resp_author.context["notes"]) == 1

    # other_user (member, not author) should see 0.
    client.logout()
    client.force_login(other_user)
    resp_other = client.get(
        reverse("notes:list", kwargs={"project_pk": project.pk})
    )
    assert resp_other.status_code == 200
    assert len(resp_other.context["notes"]) == 0


def test_render_markdown_escapes_html():
    rendered = render_markdown("<script>alert('xss')</script>")
    # The raw <script> tag must not be in the output.
    assert "<script>" not in rendered
    # The escaped form should appear.
    assert "&lt;script&gt;" in rendered
