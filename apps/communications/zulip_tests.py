"""Tests for a project's team chat: connecting it to Zulip, and writing to it.

The team chat lives in the project's Communications tab, for Leads only
for now.

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

    response = client.post(_url("zulip_connection", project), FORM)

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

    response = client.post(_url("zulip_connection", project), FORM)

    assert response.status_code == 400
    assert not ZulipConnection.objects.filter(project=project).exists()
    assert "did not accept" in response.content.decode()


@pytest.mark.django_db
def test_a_failed_change_is_shown_again_on_the_connection_page(
    client, project, pm_user, sent
):
    _connect(project)
    client.force_login(pm_user)
    sent.result = (False, "Zulip did not accept the bot's email and API key.")

    response = client.post(
        _url("zulip_connection", project), {**FORM, "topic": "elsewhere"}
    )

    assert response.status_code == 400
    assert [t.name for t in response.templates][0] == "communications/zulip_connection.html"
    assert "did not accept" in response.content.decode()
    assert ZulipConnection.objects.get(project=project).topic == "pigauto"


@pytest.mark.django_db
def test_a_failed_first_connection_is_shown_again_on_the_tab(
    client, project, pm_user, sent
):
    client.force_login(pm_user)
    sent.result = (False, "Zulip did not accept the bot's email and API key.")

    response = client.post(_url("zulip_connection", project), FORM)

    assert [t.name for t in response.templates][0] == "communications/project.html"


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

    response = client.post(_url("zulip_connection", project), {**FORM, "site": site})

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
    response = client.post(_url("zulip_connection", project), form)

    assert response.status_code == 302
    connection = ZulipConnection.objects.get(project=project)
    assert connection.api_key == "secret-key"
    assert connection.notify_wiki is True
    # Same destination, same bot: nothing to prove again.
    assert sent == []


@pytest.mark.django_db
def test_every_field_of_the_connection_form_explains_itself(client, project, pm_user):
    """Each field carries help text with an example, each text field a
    placeholder, and none of it uses an em or en dash."""
    from django.utils.html import escape

    from apps.communications.forms import ZulipConnectionForm

    form = ZulipConnectionForm()
    for name, field in form.fields.items():
        assert field.help_text, name
        if name != "enabled":
            assert "example" in field.help_text.lower(), name
        if not name.startswith("notify_") and name != "enabled":
            assert field.widget.attrs.get("placeholder"), name

    client.force_login(pm_user)
    content = client.get(_url("project", project)).content.decode()
    card = content[content.index("Connect this project to Zulip"):]
    card = card[: card.index("</form>")]
    for name, field in form.fields.items():
        assert escape(field.help_text) in card, name
    assert "\u2014" not in card and "\u2013" not in card


@pytest.mark.django_db
def test_the_key_is_never_sent_back_to_the_browser(client, project, pm_user):
    _connect(project)
    client.force_login(pm_user)

    content = client.get(_url("zulip_connection", project)).content.decode()

    assert "secret-key" not in content
    assert "iconnects-bot@cossee.zulipchat.com" in content


@pytest.mark.django_db
def test_a_contributor_cannot_connect_or_disconnect(client, project, contributor, sent):
    client.force_login(contributor)

    assert client.post(_url("zulip_connection", project), FORM).status_code == 403
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
def test_once_connected_the_tab_is_the_team_chat_and_the_connection_a_button(
    client, project, pm_user
):
    _connect(project)
    client.force_login(pm_user)

    content = client.get(_url("project", project)).content.decode()

    assert "Team chat" in content
    assert "Send to the team" in content
    # The connection is not on the tab, only a way to it.
    assert f'href="{_url("zulip_connection", project)}"' in content
    assert "Bot API key" not in content
    assert "iconnects-bot@cossee.zulipchat.com" not in content

    # Its own page has the form, Disconnect, and a way back.
    page = client.get(_url("zulip_connection", project)).content.decode()
    assert "Bot API key" in page
    assert _url("zulip_disconnect", project) in page
    assert f'href="{_url("project", project)}"' in page
    assert "Send to the team" not in page


@pytest.mark.django_db
def test_until_connected_the_tab_is_the_connection_form(client, project, pm_user):
    client.force_login(pm_user)

    content = client.get(_url("project", project)).content.decode()

    assert "Connect this project to Zulip" in content
    assert "Bot API key" in content
    assert "Send to the team" not in content
    # Nothing to open yet: the connection page sends you to the tab.
    response = client.get(_url("zulip_connection", project))
    assert response.status_code == 302
    assert response.url == _url("project", project)


@pytest.mark.django_db
def test_a_paused_connection_says_so_on_the_tab(client, project, pm_user):
    _connect(project, enabled=False)
    client.force_login(pm_user)

    content = client.get(_url("project", project)).content.decode()

    assert "is paused" in content
    assert "Send to the team" not in content
    assert f'href="{_url("zulip_connection", project)}"' in content


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


def _marked(strip, href):
    """The opening tag of the tab strip's link to ``href``."""
    tag = strip[strip.index(f'href="{href}"'):]
    return tag[: tag.index(">")]


@pytest.mark.django_db
def test_the_communications_tab_is_off_until_settings_turn_it_on(
    client, project, pm_user
):
    client.force_login(pm_user)
    detail = reverse("projects:detail", kwargs={"pk": project.pk})
    settings_url = reverse("projects:update", kwargs={"pk": project.pk})
    tab = _url("project", project)

    # Off: no tab, and its pages are reached from Settings, which they mark
    # and lead back to.
    assert f'href="{tab}"' not in _tab_strip(client.get(detail).content.decode())
    content = client.get(tab).content.decode()
    strip = _tab_strip(content)
    assert "project-tab active" in _marked(strip, settings_url)
    assert strip.count("project-tab active") == 1
    assert "&larr; Settings" in content

    # On: a tab of its own, marked while you are on it, with no way back
    # to Settings drawn.
    project.show_communications_tab = True
    project.save()
    strip = _tab_strip(client.get(detail).content.decode())
    assert f'href="{tab}"' in strip
    assert ">Communications<" in "".join(strip.split())
    content = client.get(tab).content.decode()
    strip = _tab_strip(content)
    assert "project-tab active" in _marked(strip, tab)
    assert strip.count("project-tab active") == 1
    assert "&larr; Settings" not in content
    # Not on the Project Overview itself, only in the tab strip.
    detail_content = client.get(detail).content.decode()
    assert detail_content.count(f'href="{tab}"') == 1


@pytest.mark.django_db
def test_the_connection_page_keeps_the_communications_tab_marked(
    client, project, pm_user
):
    _connect(project)
    project.show_communications_tab = True
    project.save()
    client.force_login(pm_user)

    strip = _tab_strip(client.get(_url("zulip_connection", project)).content.decode())

    assert "project-tab active" in _marked(strip, _url("project", project))
    assert strip.count("project-tab active") == 1


@pytest.mark.django_db
def test_a_lead_turns_the_tab_on_and_off_in_settings(client, project, pm_user):
    from apps.audit.models import AuditLog

    client.force_login(pm_user)
    settings_url = reverse("projects:update", kwargs={"pk": project.pk})

    content = client.get(settings_url).content.decode()
    assert 'name="show_communications_tab"' in content
    assert _url("tab", project) in content

    response = client.post(_url("tab", project), {"show_communications_tab": "on"})
    assert response.status_code == 302
    assert response.url == settings_url
    project.refresh_from_db()
    assert project.show_communications_tab is True
    assert AuditLog.objects.filter(
        project=project, metadata__show_communications_tab=True
    ).exists()
    # Drawn switched on.
    toggle = client.get(settings_url).content.decode()
    toggle = toggle[toggle.index('id="id_show_communications_tab"'):]
    toggle = toggle[: toggle.index(">")]
    assert "checked" in toggle

    # An unticked checkbox posts nothing: off.
    client.post(_url("tab", project), {})
    project.refresh_from_db()
    assert project.show_communications_tab is False


@pytest.mark.django_db
def test_a_contributor_cannot_turn_the_tab_on(client, project, contributor):
    client.force_login(contributor)

    response = client.post(_url("tab", project), {"show_communications_tab": "on"})

    assert response.status_code == 403
    project.refresh_from_db()
    assert project.show_communications_tab is False


@pytest.mark.django_db
def test_a_contributor_has_no_communications_tab(client, project, contributor):
    _connect(project)
    project.show_communications_tab = True
    project.save()
    client.force_login(contributor)

    content = client.get(
        reverse("projects:detail", kwargs={"pk": project.pk})
    ).content.decode()

    assert _url("project", project) not in content


@pytest.mark.django_db
def test_the_email_log_keeps_the_contributors_tab_marked(client, project, pm_user):
    client.force_login(pm_user)
    members = reverse("projects:members", kwargs={"pk": project.pk})

    strip = _tab_strip(client.get(_url("email_log", project)).content.decode())
    marked = strip[strip.index(f'href="{members}"'):]
    marked = marked[: marked.index(">")]
    assert "project-tab active" in marked
    assert strip.count("project-tab active") == 1


@pytest.mark.django_db
def test_settings_points_a_lead_to_the_connection(client, project, pm_user):
    client.force_login(pm_user)
    settings_url = reverse("projects:update", kwargs={"pk": project.pk})

    assert "Connect to Zulip" in client.get(settings_url).content.decode()

    _connect(project)
    content = client.get(settings_url).content.decode()
    assert "Manage Zulip connection" in content
    assert f'href="{_url("zulip_connection", project)}"' in content
    assert "Open Communications" in content
    assert "shinichi-projects" in content
