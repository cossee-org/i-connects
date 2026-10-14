"""Tests for the communications module and the Surveys rename.

The module itself is empty, so what there is to guard is its shape: both
surfaces answer, the project one is members-only, and the navigation names
the two modules and reaches them. The label assertions are here because a
rename regresses silently — nothing else fails when a tab goes back to
saying "Forms".
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.enums import MemberPrivileges, ProjectStatus
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner


@pytest.fixture
def project(pm_user):
    return create_project_with_owner(
        user=pm_user,
        form_data={
            "name": "Comms Project",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )


@pytest.fixture
def contributor(project, user):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    return user


def _project_url(project):
    return reverse("communications:project", kwargs={"project_pk": project.pk})


# ---------------------------------------------------------------------------
# The sidebar
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_sidebar_carries_surveys_then_communications(client, user):
    client.force_login(user)

    content = client.get(reverse("projects:dashboard")).content.decode()

    sidebar = content[content.index('aria-label="Primary"'):]
    sidebar = sidebar[: sidebar.index("</nav>")]

    assert reverse("forms_builder:form_list") in sidebar
    assert reverse("communications:list") in sidebar
    # Communications sits after Surveys, and neither label is singular or
    # stale. "Surveys" contains "Survey", so the singular is checked as a
    # whole label (">Survey<" with the sidebar's whitespace stripped).
    assert sidebar.index("Surveys") < sidebar.index("Communications")
    compact = "".join(sidebar.split())
    assert ">Survey<" not in compact
    assert ">Communication<" not in compact
    assert "Forms" not in sidebar


@pytest.mark.django_db
def test_the_sidebar_marks_each_module_active_on_its_own_page(client, user):
    client.force_login(user)

    surveys = client.get(reverse("forms_builder:form_list")).content.decode()
    comms = client.get(reverse("communications:list")).content.decode()

    surveys_link = f'href="{reverse("forms_builder:form_list")}" class="sidebar-link active"'
    comms_link = f'href="{reverse("communications:list")}" class="sidebar-link active"'
    assert surveys_link in surveys
    assert comms_link in comms
    # And neither claims the other's page.
    assert comms_link not in surveys
    assert surveys_link not in comms


# ---------------------------------------------------------------------------
# The module page
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_communications_page_needs_a_signed_in_user(client):
    response = client.get(reverse("communications:list"))

    assert response.status_code == 302
    assert "/login" in response.url or "/accounts/" in response.url


@pytest.mark.django_db
def test_the_communications_page_is_an_empty_state_for_now(client, user):
    client.force_login(user)

    response = client.get(reverse("communications:list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert ">Communications</h1>" in content
    assert ">Communication</h1>" not in content
    assert "Coming soon" in content


@pytest.mark.django_db
def test_the_surveys_page_answers_under_its_new_name(client, user):
    client.force_login(user)

    response = client.get(reverse("forms_builder:form_list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert ">Surveys</h1>" in content
    assert ">Survey</h1>" not in content
    assert ">Forms</h1>" not in content


# ---------------------------------------------------------------------------
# The project section
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_member_sees_the_project_communications_section(client, project, pm_user):
    client.force_login(pm_user)

    response = client.get(_project_url(project))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Coming soon" in content
    # The project chrome comes with it: header, and the tab strip with this
    # tab marked as the one you are on.
    assert project.name in content
    assert 'aria-label="Project sections"' in content
    assert "project-tab active" in content


@pytest.mark.django_db
def test_a_contributor_sees_it_too(client, project, contributor):
    client.force_login(contributor)

    assert client.get(_project_url(project)).status_code == 200


@pytest.mark.django_db
def test_the_project_section_is_members_only(client, project, other_user):
    client.force_login(other_user)

    assert client.get(_project_url(project)).status_code == 403


@pytest.mark.django_db
def test_the_project_tab_strip_names_both_modules(client, project, pm_user):
    client.force_login(pm_user)

    content = client.get(
        reverse("projects:detail", kwargs={"pk": project.pk})
    ).content.decode()

    assert reverse("projects:project_forms", kwargs={"pk": project.pk}) in content
    assert _project_url(project) in content
    strip = content[content.index('aria-label="Project sections"'):]
    strip = strip[: strip.index("</nav>")]
    compact = "".join(strip.split())
    assert ">Surveys<" in compact
    assert ">Communications<" in compact
    assert ">Survey<" not in compact
    assert ">Communication<" not in compact
    assert "Forms" not in strip
    # Communications comes after Surveys, and Settings stays last.
    assert strip.index("Surveys") < strip.index("Communications") < strip.index("Settings")


# ---------------------------------------------------------------------------
# Tab headings
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_every_project_tab_opens_with_its_heading_and_info(client, project, pm_user):
    """Each tab starts with an <h2> naming it, in the one shared style, with
    its description behind an (i) beside it rather than in a paragraph."""
    client.force_login(pm_user)
    pk = project.pk
    pages = {
        "Project Overview": reverse("projects:detail", kwargs={"pk": pk}),
        "Tasks": reverse("tasks:board", kwargs={"project_pk": pk}),
        "Contributors": reverse("projects:members", kwargs={"pk": pk}),
        "Contribution Tracking": reverse("projects:contribution_tracking", kwargs={"pk": pk}),
        "Wiki": reverse("wiki:list", kwargs={"project_pk": pk}),
        "Surveys": reverse("projects:project_forms", kwargs={"pk": pk}),
        "Communications": _project_url(project),
        "Settings": reverse("projects:update", kwargs={"pk": pk}),
    }
    for title, url in pages.items():
        response = client.get(url)
        assert response.status_code == 200, url
        heading = f'<h2 class="text-lg font-semibold text-brand-navy">{title}</h2>'
        content = response.content.decode()
        assert heading in content, title
        assert f'aria-label="About {title}"' in content, title


@pytest.mark.django_db
def test_creating_a_project_has_no_settings_heading(client, pm_user):
    """The settings form doubles as the create form, which is not a tab."""
    client.force_login(pm_user)
    content = client.get(reverse("projects:create")).content.decode()
    assert ">Settings</h2>" not in content
