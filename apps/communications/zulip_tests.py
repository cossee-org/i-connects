"""Tests for a project's team chat: connecting it to Zulip, and writing to it.

The team chat lives in the project's Communications section, a Leads-only
subsection of the Project tab.

Nothing here reaches the network — ``zulip.deliver`` is replaced by a
recorder, which is also how the tests see what would have been sent.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.core.enums import MemberPrivileges, ProjectStatus
from apps.integrations import zulip
from apps.integrations.models import ZulipConnection
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner


@pytest.fixture
def project(pm_user):
    return create_project_with_owner(
        user=pm_user,
        form_data={"name": "Pigauto", "status": ProjectStatus.ACTIVE, "is_public": True},
    )


@pytest.fixture
def contributor(project, user):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    return user


@pytest.fixture
def sent(monkeypatch):
    """Messages handed to Zulip, as ``(connection, content)``; the fake
    succeeds unless a test sets ``sent.result``."""

    class Outbox(list):
        result = (True, "")

    outbox = Outbox()

    def fake_deliver(connection, content, **kwargs):
        outbox.append((connection, content))
        return outbox.result

    monkeypatch.setattr(zulip, "deliver", fake_deliver)
    return outbox


def _connect(project, **overrides):
    values = {
        "site": "https://cossee.zulipchat.com",
        "bot_email": "iconnects-bot@cossee.zulipchat.com",
        "api_key": "secret-key",
        "channel": "shinichi-projects",
        "topic": "pigauto",
    }
    values.update(overrides)
    return ZulipConnection.objects.create(project=project, **values)


def _url(name, project):
    return reverse(f"communications:{name}", kwargs={"project_pk": project.pk})


FORM = {
    "site": "https://cossee.zulipchat.com/",
    "bot_email": "iconnects-bot@cossee.zulipchat.com",
    "api_key": "secret-key",
    "channel": "#shinichi-projects",
    "topic": "pigauto",
    "notify_tasks": "on",
    "notify_deadlines": "on",
    "enabled": "on",
}


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_lead_connects_the_project_and_the_topic_is_told(client, project, pm_user, sent):
    client.force_login(pm_user)

    response = client.post(_url("zulip_save", project), FORM)

    assert response.status_code == 302
    connection = ZulipConnection.objects.get(project=project)
    # Tidied on the way in: no trailing slash, no "#".
    assert connection.site == "https://cossee.zulipchat.com"
    assert connection.channel == "shinichi-projects"
    assert connection.topic == "pigauto"
    # Unticked boxes are off.
    assert connection.notify_tasks is True
    assert connection.notify_comments is False
    # The proof-of-connection message went to the new destination.
    assert len(sent) == 1
    assert "now connected" in sent[0][1]
    assert sent[0][0].channel == "shinichi-projects"


@pytest.mark.django_db
def test_a_connection_that_cannot_post_is_not_saved(client, project, pm_user, sent):
    client.force_login(pm_user)
    sent.result = (False, "Zulip did not accept the bot's email and API key.")

    response = client.post(_url("zulip_save", project), FORM)

    assert response.status_code == 400
    assert not ZulipConnection.objects.filter(project=project).exists()
    assert "did not accept" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "site",
    [
        "http://cossee.zulipchat.com",  # not https
        "https://localhost:8000",  # an internal host
        "https://zulipchat.com.evil.example",  # lookalike
    ],
)
def test_only_an_allowed_zulip_host_is_accepted(client, project, pm_user, sent, site):
    client.force_login(pm_user)

    response = client.post(_url("zulip_save", project), {**FORM, "site": site})

    assert response.status_code == 400
    assert not ZulipConnection.objects.filter(project=project).exists()
    assert sent == []


@pytest.mark.django_db
def test_editing_keeps_the_key_and_sends_no_second_test_message(
    client, project, pm_user, sent
):
    _connect(project)
    client.force_login(pm_user)

    form = {**FORM, "api_key": "", "channel": "shinichi-projects", "notify_wiki": "on"}
    response = client.post(_url("zulip_save", project), form)

    assert response.status_code == 302
    connection = ZulipConnection.objects.get(project=project)
    assert connection.api_key == "secret-key"
    assert connection.notify_wiki is True
    # Same destination, same bot: nothing to prove again.
    assert sent == []


@pytest.mark.django_db
def test_the_key_is_never_sent_back_to_the_browser(client, project, pm_user):
    _connect(project)
    client.force_login(pm_user)

    content = client.get(_url("project", project)).content.decode()

    assert "secret-key" not in content
    assert "iconnects-bot@cossee.zulipchat.com" in content


@pytest.mark.django_db
def test_a_contributor_cannot_connect_or_disconnect(client, project, contributor, sent):
    client.force_login(contributor)

    assert client.post(_url("zulip_save", project), FORM).status_code == 403
    assert not ZulipConnection.objects.filter(project=project).exists()

    _connect(project)
    assert client.post(_url("zulip_disconnect", project)).status_code == 403
    assert ZulipConnection.objects.filter(project=project).exists()

    # Nor can they open the section and see the bot's details.
    response = client.get(_url("project", project))
    assert response.status_code == 403
    assert "iconnects-bot@cossee.zulipchat.com" not in response.content.decode()


@pytest.mark.django_db
def test_a_lead_disconnects(client, project, pm_user):
    _connect(project)
    client.force_login(pm_user)

    response = client.post(_url("zulip_disconnect", project))

    assert response.status_code == 302
    assert not ZulipConnection.objects.filter(project=project).exists()


# ---------------------------------------------------------------------------
# The team chat
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_lead_finds_the_link_to_the_topic(client, project, pm_user):
    _connect(project, channel="team/cossee.acb", topic="pig auto")
    client.force_login(pm_user)

    content = client.get(_url("project", project)).content.decode()

    assert "Open in Zulip" in content
    # Zulip's own link encoding: "/" -> .2F, "." -> .2E, " " -> .20
    assert (
        "https://cossee.zulipchat.com/#narrow/channel/team.2Fcossee.2Eacb/topic/pig.20auto"
        in content
    )


@pytest.mark.django_db
def test_without_a_connection_a_lead_is_offered_one(client, project, pm_user):
    client.force_login(pm_user)

    content = client.get(_url("project", project)).content.decode()

    assert "Connect this project to Zulip" in content
    assert "Open in Zulip" not in content


@pytest.mark.django_db
def test_a_lead_writes_to_the_team(client, project, pm_user, sent):
    _connect(project)
    client.force_login(pm_user)

    response = client.post(
        _url("team_message", project), {"text": "Draft is ready, @**all** please read"}
    )

    assert response.status_code == 302
    assert len(sent) == 1
    content = sent[0][1]
    assert content.startswith("**PM** (via i-CONNECTS):")
    assert "Draft is ready" in content
    # A mention typed into the box is text, not a ping for the whole channel.
    assert "@**all**" not in content


@pytest.mark.django_db
def test_a_message_goes_nowhere_when_the_project_is_not_connected(
    client, project, pm_user, sent
):
    client.force_login(pm_user)

    response = client.post(_url("team_message", project), {"text": "Hello"})

    assert response.status_code == 302
    assert sent == []


@pytest.mark.django_db
def test_the_section_is_for_leads_only_for_now(client, project, contributor, sent):
    _connect(project)
    client.force_login(contributor)

    assert client.get(_url("project", project)).status_code == 403
    response = client.post(_url("team_message", project), {"text": "Hello"})
    assert response.status_code == 403
    assert sent == []


@pytest.mark.django_db
def test_an_outsider_cannot_write_to_the_team(client, project, other_user, sent):
    _connect(project)
    client.force_login(other_user)

    response = client.post(_url("team_message", project), {"text": "Hello"})

    assert response.status_code == 403
    assert sent == []


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
def _tab_strip(content):
    strip = content[content.index('aria-label="Project sections"'):]
    return strip[: strip.index("</nav>")]


@pytest.mark.django_db
@override_settings(SHOW_SURVEYS_AND_COMMUNICATIONS=True)
def test_communications_is_a_subsection_of_the_project_tab(client, project, pm_user):
    _connect(project)
    client.force_login(pm_user)
    detail = reverse("projects:detail", kwargs={"pk": project.pk})
    section = _url("project", project)

    # No tab of its own, even with the module shown and a team chat…
    detail_content = client.get(detail).content.decode()
    assert f'href="{section}"' not in _tab_strip(detail_content)
    assert ">Communications<" not in "".join(_tab_strip(detail_content).split())
    # …but a way in from the Project Overview…
    assert f'href="{section}"' in detail_content

    # …and the section keeps the Project tab marked, with a way back.
    content = client.get(section).content.decode()
    strip = _tab_strip(content)
    project_tab = strip[strip.index(f'href="{detail}"'):]
    project_tab = project_tab[: project_tab.index(">")]
    assert "project-tab active" in project_tab
    assert strip.count("project-tab active") == 1
    assert "Project overview" in content


@pytest.mark.django_db
def test_only_a_lead_is_shown_the_way_in(client, project, contributor):
    _connect(project)
    client.force_login(contributor)

    content = client.get(
        reverse("projects:detail", kwargs={"pk": project.pk})
    ).content.decode()

    assert _url("project", project) not in content


@pytest.mark.django_db
def test_settings_points_a_lead_to_the_connection(client, project, pm_user):
    client.force_login(pm_user)
    settings_url = reverse("projects:update", kwargs={"pk": project.pk})

    assert "Connect to Zulip" in client.get(settings_url).content.decode()

    _connect(project)
    content = client.get(settings_url).content.decode()
    assert "Manage Zulip connection" in content
    assert "shinichi-projects" in content
