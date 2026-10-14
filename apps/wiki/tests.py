"""Tests for the wiki app.

Covers the two things the wiki promises — that a page is editable by the
project and that nothing an edit overwrites is lost — plus the permission
boundaries around them.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import MemberPrivileges, ProjectStatus
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner
from apps.wiki.models import WikiPage, WikiRevision
from apps.wiki.services import (
    EditConflict,
    create_page,
    delete_page,
    link_resolver,
    restore_revision,
    unique_slug,
    update_page,
)
from apps.wiki.utils import excerpt, render_wiki_markdown, summarise_change


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _project(owner):
    return create_project_with_owner(
        user=owner,
        form_data={
            "name": "Wiki Project",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )


def _add_contributor(project, user):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    return member


@pytest.fixture
def project(pm_user):
    return _project(pm_user)


@pytest.fixture
def contributor(project, user):
    _add_contributor(project, user)
    return user


def _url(name, project, **kwargs):
    return reverse(f"wiki:{name}", kwargs={"project_pk": project.pk, **kwargs})


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_slug_is_derived_from_the_title_and_deduped(project, pm_user):
    first = create_page(project=project, actor=pm_user, title="Sampling Protocol")
    second = create_page(project=project, actor=pm_user, title="Sampling protocol")

    assert first.slug == "sampling-protocol"
    assert second.slug == "sampling-protocol-2"


@pytest.mark.django_db
def test_slug_never_collides_with_the_new_page_route(project, pm_user):
    """A page at ``/wiki/new/`` would be shadowed by the create form."""
    page = create_page(project=project, actor=pm_user, title="New")

    assert page.slug == "new-2"


@pytest.mark.django_db
def test_slug_falls_back_when_a_title_slugifies_to_nothing(project, pm_user):
    page = create_page(project=project, actor=pm_user, title="!!!")

    assert page.slug == "page"


@pytest.mark.django_db
def test_a_slug_is_only_taken_within_its_own_project(pm_user, other_user):
    one = _project(pm_user)
    two = _project(other_user)

    assert unique_slug(one, "Protocol") == "protocol"
    create_page(project=one, actor=pm_user, title="Protocol")
    assert unique_slug(two, "Protocol") == "protocol"


@pytest.mark.django_db
def test_renaming_a_page_leaves_its_address_alone(project, pm_user):
    """Links into the wiki keep working across a rename."""
    page = create_page(project=project, actor=pm_user, title="Old name")

    page, _ = update_page(
        page=page, actor=pm_user, title="New name", content_markdown=""
    )

    assert page.title == "New name"
    assert page.slug == "old-name"


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_creating_a_page_writes_its_first_revision(project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="First."
    )

    revisions = list(page.revisions.all())
    assert len(revisions) == 1
    assert revisions[0].number == 1
    assert revisions[0].content_markdown == "First."
    assert revisions[0].editor == pm_user
    assert page.latest_revision_number == 1


@pytest.mark.django_db
def test_each_edit_appends_a_revision_and_keeps_the_old_text(project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )

    page, wrote_revision = update_page(
        page=page,
        actor=pm_user,
        title="Protocol",
        content_markdown="v2",
        summary="second pass",
    )

    assert wrote_revision is True
    numbers = list(page.revisions.values_list("number", flat=True))
    assert numbers == [2, 1]
    assert page.content_markdown == "v2"
    assert page.revisions.get(number=1).content_markdown == "v1"
    assert page.revisions.get(number=2).summary == "second pass"


@pytest.mark.django_db
def test_a_save_that_changes_nothing_writes_no_revision(project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="same"
    )

    page, wrote_revision = update_page(
        page=page, actor=pm_user, title="Protocol", content_markdown="same"
    )

    assert wrote_revision is False
    assert page.revisions.count() == 1


@pytest.mark.django_db
def test_locking_a_page_is_a_change_without_a_new_revision(project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="text"
    )

    page, wrote_revision = update_page(
        page=page,
        actor=pm_user,
        title="Protocol",
        content_markdown="text",
        locked=True,
    )

    assert page.locked is True
    assert wrote_revision is False
    assert page.revisions.count() == 1


@pytest.mark.django_db
def test_a_revision_cannot_be_rewritten(project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    revision = page.revisions.get(number=1)

    revision.content_markdown = "tampered"
    with pytest.raises(IntegrityError):
        revision.save()

    assert WikiRevision.objects.get(pk=revision.pk).content_markdown == "v1"


@pytest.mark.django_db
def test_a_stale_editor_is_told_rather_than_overwriting(project, pm_user, other_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    # Someone else saves while our editor has revision 1 open.
    update_page(page=page, actor=other_user, title="Protocol", content_markdown="v2")

    with pytest.raises(EditConflict) as excinfo:
        update_page(
            page=page,
            actor=pm_user,
            title="Protocol",
            content_markdown="my version",
            expected_revision=1,
        )

    assert excinfo.value.expected == 1
    assert excinfo.value.current == 2
    assert WikiPage.objects.get(pk=page.pk).content_markdown == "v2"


@pytest.mark.django_db
def test_restoring_a_revision_moves_history_forward(project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    page, _ = update_page(
        page=page, actor=pm_user, title="Protocol v2", content_markdown="v2"
    )
    first = page.revisions.get(number=1)

    page = restore_revision(page=page, revision=first, actor=pm_user)

    assert page.content_markdown == "v1"
    assert page.title == "Protocol"
    # Nothing was rewound: revision 2 is still on the record, and the
    # restore is revision 3.
    assert list(page.revisions.values_list("number", flat=True)) == [3, 2, 1]
    assert page.revisions.get(number=2).content_markdown == "v2"
    assert page.revisions.get(number=3).summary == "Restored revision 1"


@pytest.mark.django_db
def test_a_revision_from_another_page_cannot_be_restored(project, pm_user):
    one = create_page(project=project, actor=pm_user, title="One")
    two = create_page(project=project, actor=pm_user, title="Two")

    with pytest.raises(ValueError):
        restore_revision(page=one, revision=two.revisions.first(), actor=pm_user)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_every_mutation_is_audit_logged(project, pm_user):
    page = create_page(project=project, actor=pm_user, title="Protocol")
    update_page(page=page, actor=pm_user, title="Protocol", content_markdown="text")
    restore_revision(page=page, revision=page.revisions.get(number=1), actor=pm_user)
    delete_page(page=page, actor=pm_user)

    actions = list(
        AuditLog.objects.filter(project=project, action__startswith="wiki.")
        .order_by("id")
        .values_list("action", flat=True)
    )
    assert actions == [
        AuditAction.WIKI_PAGE_CREATED,
        AuditAction.WIKI_PAGE_UPDATED,
        AuditAction.WIKI_PAGE_RESTORED,
        AuditAction.WIKI_PAGE_DELETED,
    ]


@pytest.mark.django_db
def test_deleting_a_page_takes_its_history_but_leaves_the_audit_entry(
    project, pm_user
):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    update_page(page=page, actor=pm_user, title="Protocol", content_markdown="v2")

    delete_page(page=page, actor=pm_user)

    assert not WikiPage.objects.filter(pk=page.pk).exists()
    assert not WikiRevision.objects.filter(page_id=page.pk).exists()
    entry = AuditLog.objects.get(action=AuditAction.WIKI_PAGE_DELETED)
    assert entry.metadata["title"] == "Protocol"
    assert entry.metadata["revisions"] == 2


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_raw_html_in_page_text_is_escaped_not_rendered():
    rendered = render_wiki_markdown("<script>alert('x')</script>\n\n**bold**")

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "<strong>bold</strong>" in rendered.html


def test_a_contents_list_appears_only_when_there_are_headings():
    assert render_wiki_markdown("just text", with_toc=True).toc == ""
    assert "#a-heading" in render_wiki_markdown(
        "## A heading\n\ntext", with_toc=True
    ).toc


def test_markdown_syntax_that_needs_an_unescaped_character_still_works():
    """Blockquotes are ``>``-led, so over-escaping the source loses them."""
    rendered = render_wiki_markdown("> A quoted note\n\n5 > 3 and a & b")

    assert "<blockquote>" in rendered.html
    assert "A quoted note" in rendered.html
    # And a bare comparison in prose is still text, correctly serialised.
    assert "5 &gt; 3 and a &amp; b" in rendered.html


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "JavaScript:alert(1)", "java\tscript:alert(1)", "data:text/html,x"],
)
def test_a_link_to_a_script_scheme_is_neutralised(url):
    """Markdown does not police link targets, so this needs no raw HTML."""
    rendered = render_wiki_markdown(f"[go]({url})")

    assert 'href=""' in rendered.html
    assert "javascript" not in rendered.html.lower()
    assert "data:" not in rendered.html


@pytest.mark.parametrize(
    "url", ["https://example.com/x", "mailto:a@b.test", "../sibling/", "#a-heading"]
)
def test_ordinary_link_targets_are_left_alone(url):
    assert f'href="{url}"' in render_wiki_markdown(f"[go]({url})").html


def test_an_image_with_an_unsafe_source_is_neutralised():
    rendered = render_wiki_markdown("![x](javascript:alert(1))")

    assert 'src=""' in rendered.html


def test_excerpt_reads_as_prose_not_as_markdown():
    text = "## Heading\n\nSome **bold** text with [[Other|a link]] and `code`."

    # Headings and list items gain the full stop they never needed on their
    # own line, so the one-line excerpt does not run two of them together.
    assert excerpt(text) == "Heading. Some bold text with a link and code."


def test_excerpt_is_truncated_with_an_ellipsis():
    assert excerpt("word " * 100, limit=20).endswith("…")
    assert len(excerpt("word " * 100, limit=20)) == 20


def test_change_summary_counts_lines_and_is_empty_when_unchanged():
    assert summarise_change("a\nb", "a\nb") == ""
    assert summarise_change("a\nb", "a\nc\nd") == "+2 −1 lines"
    assert summarise_change("", "a") == "+1 line"


@pytest.mark.django_db
def test_wiki_links_resolve_on_title_then_slug(project, pm_user):
    page = create_page(project=project, actor=pm_user, title="Sampling Protocol")
    resolve = link_resolver(project)

    # Exact title, a different case, and the slug all reach the page.
    for target in ("Sampling Protocol", "sampling protocol", "sampling-protocol"):
        url, exists = resolve(target)
        assert exists is True, target
        assert url == page.get_absolute_url()


@pytest.mark.django_db
def test_a_link_to_a_missing_page_offers_to_create_it(project, pm_user):
    resolve = link_resolver(project)

    url, exists = resolve("Not written yet")

    assert exists is False
    assert url == (
        _url("create", project) + "?title=Not+written+yet"
    )


@pytest.mark.django_db
def test_a_renamed_page_is_still_reachable_by_its_new_name(project, pm_user):
    page = create_page(project=project, actor=pm_user, title="Old name")
    update_page(page=page, actor=pm_user, title="New name", content_markdown="")

    _, exists = link_resolver(project)("New name")

    assert exists is True


# ---------------------------------------------------------------------------
# Views: access
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_wiki_is_members_only(client, project, other_user):
    client.force_login(other_user)

    response = client.get(_url("list", project))

    assert response.status_code == 403


@pytest.mark.django_db
def test_a_signed_out_visitor_is_sent_to_log_in(client, project):
    response = client.get(_url("list", project))

    assert response.status_code == 302
    assert "/login" in response.url or "/accounts/" in response.url


@pytest.mark.django_db
def test_a_member_sees_the_index_with_its_pages(client, project, pm_user):
    create_page(
        project=project,
        actor=pm_user,
        title="Sampling protocol",
        content_markdown="How we sample.",
    )
    client.force_login(pm_user)

    response = client.get(_url("list", project))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Sampling protocol" in content
    assert "How we sample." in content


@pytest.mark.django_db
def test_the_project_tab_strip_points_at_the_wiki(client, project, pm_user):
    client.force_login(pm_user)

    response = client.get(_url("list", project))
    content = response.content.decode()

    assert 'aria-label="Project sections"' in content
    assert _url("list", project) in content
    assert "project-tab active" in content


@pytest.mark.django_db
def test_the_index_searches_titles_and_page_text(client, project, pm_user):
    create_page(
        project=project, actor=pm_user, title="Fieldwork", content_markdown="quadrats"
    )
    create_page(
        project=project, actor=pm_user, title="Analysis", content_markdown="mixed models"
    )
    client.force_login(pm_user)

    # Read the results partial rather than the whole page: the index's own
    # help text mentions example page titles, which would match either way.
    def results(query):
        return client.get(
            _url("list", project), {"q": query}, headers={"HX-Request": "true"}
        ).content.decode()

    by_title, by_body = results("Analys"), results("quadrats")

    assert "Analysis" in by_title and "Fieldwork" not in by_title
    assert "Fieldwork" in by_body and "Analysis" not in by_body


@pytest.mark.django_db
def test_search_swaps_only_the_results_for_an_htmx_request(client, project, pm_user):
    create_page(project=project, actor=pm_user, title="Sampling")
    client.force_login(pm_user)

    response = client.get(
        _url("list", project), {"q": "Sampling"}, headers={"HX-Request": "true"}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Sampling" in content
    # The partial, not the whole page: no page chrome came back with it.
    assert "<html" not in content
    assert 'aria-label="Project sections"' not in content


# ---------------------------------------------------------------------------
# Views: writing
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_contributor_can_create_a_page(client, project, contributor):
    client.force_login(contributor)

    response = client.post(
        _url("create", project),
        {"title": "Onboarding", "content_markdown": "Start here.", "summary": "first"},
    )

    page = WikiPage.objects.get(project=project, title="Onboarding")
    assert response.status_code == 302
    assert response.url == page.get_absolute_url()
    assert page.created_by == contributor
    assert page.revisions.get(number=1).summary == "first"


@pytest.mark.django_db
def test_the_create_form_prefills_a_title_from_a_missing_link(
    client, project, pm_user
):
    client.force_login(pm_user)

    response = client.get(_url("create", project), {"title": "Data dictionary"})

    assert response.status_code == 200
    assert 'value="Data dictionary"' in response.content.decode()


@pytest.mark.django_db
def test_a_page_needs_a_title(client, project, pm_user):
    client.force_login(pm_user)

    response = client.post(
        _url("create", project), {"title": "   ", "content_markdown": "text"}
    )

    assert response.status_code == 200
    assert not WikiPage.objects.filter(project=project).exists()


@pytest.mark.django_db
def test_a_contributor_can_edit_an_unlocked_page(client, project, pm_user, contributor):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    client.force_login(contributor)

    response = client.post(
        _url("update", project, slug=page.slug),
        {
            "title": "Protocol",
            "content_markdown": "v2",
            "summary": "clarified step 3",
            "base_revision": 1,
        },
    )

    page.refresh_from_db()
    assert response.status_code == 302
    assert page.content_markdown == "v2"
    assert page.updated_by == contributor
    assert page.revisions.count() == 2


@pytest.mark.django_db
def test_a_contributor_cannot_edit_a_locked_page(client, project, pm_user, contributor):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    update_page(
        page=page, actor=pm_user, title="Protocol", content_markdown="v1", locked=True
    )
    client.force_login(contributor)

    get_response = client.get(_url("update", project, slug=page.slug))
    post_response = client.post(
        _url("update", project, slug=page.slug),
        {"title": "Protocol", "content_markdown": "sneaky", "base_revision": 1},
    )

    page.refresh_from_db()
    assert get_response.status_code == 403
    assert post_response.status_code == 403
    assert page.content_markdown == "v1"


@pytest.mark.django_db
def test_a_lead_can_edit_a_locked_page(client, project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    update_page(
        page=page, actor=pm_user, title="Protocol", content_markdown="v1", locked=True
    )
    client.force_login(pm_user)

    response = client.post(
        _url("update", project, slug=page.slug),
        {
            "title": "Protocol",
            "content_markdown": "v2",
            "locked": "on",
            "base_revision": page.latest_revision_number,
        },
    )

    page.refresh_from_db()
    assert response.status_code == 302
    assert page.content_markdown == "v2"
    assert page.locked is True


@pytest.mark.django_db
def test_a_contributors_edit_leaves_the_lead_lock_alone(
    client, project, pm_user, contributor
):
    """The lock box is not on a contributor's form, so it cannot be cleared."""
    page = create_page(
        project=project, actor=pm_user, title="Notes", content_markdown="v1"
    )
    client.force_login(contributor)

    client.post(
        _url("update", project, slug=page.slug),
        {"title": "Notes", "content_markdown": "v2", "base_revision": 1, "locked": ""},
    )

    page.refresh_from_db()
    assert page.locked is False
    # And a lead's lock survives a later contributor edit.
    update_page(
        page=page, actor=pm_user, title="Notes", content_markdown="v2", locked=True
    )
    client.post(
        _url("update", project, slug=page.slug),
        {"title": "Notes", "content_markdown": "v3", "base_revision": 2},
    )
    page.refresh_from_db()
    assert page.locked is True


@pytest.mark.django_db
def test_putting_a_page_on_the_overview_is_a_change_without_a_new_revision(
    project, pm_user
):
    page = create_page(project=project, actor=pm_user, title="Protocol", content_markdown="t")

    page, wrote_revision = update_page(
        page=page, actor=pm_user, title="Protocol", content_markdown="t",
        show_on_overview=True,
    )

    assert page.show_on_overview is True
    assert wrote_revision is False
    assert page.revisions.count() == 1


@pytest.mark.django_db
def test_only_a_lead_puts_a_page_on_the_overview(client, project, pm_user, contributor):
    """A lead ticks the box; a contributor's form has no box, so their edit
    leaves the setting as the lead left it."""
    client.force_login(pm_user)
    client.post(
        _url("create", project),
        {"title": "Agreement", "content_markdown": "v1", "show_on_overview": "on"},
    )
    page = WikiPage.objects.get(project=project, title="Agreement")
    assert page.show_on_overview is True
    detail = client.get(_url("detail", project, slug=page.slug)).content.decode()
    assert "On Project Overview" in detail

    client.force_login(contributor)
    form = client.get(_url("update", project, slug=page.slug)).content.decode()
    assert 'name="show_on_overview"' not in form
    client.post(
        _url("update", project, slug=page.slug),
        {"title": "Agreement", "content_markdown": "v2", "base_revision": 1,
         "show_on_overview": ""},
    )
    page.refresh_from_db()
    assert page.content_markdown == "v2"
    assert page.show_on_overview is True


@pytest.mark.django_db
def test_a_conflicting_save_is_refused_with_an_explanation(
    client, project, pm_user, contributor
):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    update_page(
        page=page, actor=pm_user, title="Protocol", content_markdown="theirs"
    )
    client.force_login(contributor)

    response = client.post(
        _url("update", project, slug=page.slug),
        {"title": "Protocol", "content_markdown": "mine", "base_revision": 1},
    )
    content = response.content.decode()

    page.refresh_from_db()
    assert response.status_code == 200
    assert "Someone else saved this page" in content
    assert page.content_markdown == "theirs"
    # The form comes back pointed at the page as it now stands, so a second
    # press of Save applies the editor's text on top rather than looping.
    assert 'name="base_revision" value="2"' in content


# ---------------------------------------------------------------------------
# Views: reading a page
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_page_renders_its_markdown_and_its_wiki_links(client, project, pm_user):
    create_page(project=project, actor=pm_user, title="Sampling")
    page = create_page(
        project=project,
        actor=pm_user,
        title="Protocol",
        content_markdown=(
            "## Method\n\nSee [[Sampling]] and [[Data dictionary]].\n"
        ),
    )
    client.force_login(pm_user)

    content = client.get(page.get_absolute_url()).content.decode()

    assert '<h2 id="method">Method</h2>' in content
    assert _url("detail", project, slug="sampling") in content
    assert "wiki-link-missing" in content
    # The page offers to fill the gap the missing link left.
    assert "Data dictionary" in content


@pytest.mark.django_db
def test_a_page_from_another_project_is_not_reachable(client, pm_user, other_user):
    mine = _project(pm_user)
    theirs = _project(other_user)
    page = create_page(project=theirs, actor=other_user, title="Theirs")
    client.force_login(pm_user)

    response = client.get(_url("detail", mine, slug=page.slug))

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Views: deletion
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_lead_can_delete_any_page(client, project, pm_user, contributor):
    page = create_page(project=project, actor=contributor, title="Draft")
    client.force_login(pm_user)

    response = client.post(_url("delete", project, slug=page.slug))

    assert response.status_code == 302
    assert response.url == _url("list", project)
    assert not WikiPage.objects.filter(pk=page.pk).exists()


@pytest.mark.django_db
def test_an_author_can_delete_their_own_page(client, project, contributor):
    page = create_page(project=project, actor=contributor, title="Draft")
    client.force_login(contributor)

    response = client.post(_url("delete", project, slug=page.slug))

    assert response.status_code == 302
    assert not WikiPage.objects.filter(pk=page.pk).exists()


@pytest.mark.django_db
def test_a_contributor_cannot_delete_someone_elses_page(
    client, project, pm_user, contributor
):
    page = create_page(project=project, actor=pm_user, title="Protocol")
    client.force_login(contributor)

    get_response = client.get(_url("delete", project, slug=page.slug))
    post_response = client.post(_url("delete", project, slug=page.slug))

    assert get_response.status_code == 403
    assert post_response.status_code == 403
    assert WikiPage.objects.filter(pk=page.pk).exists()


# ---------------------------------------------------------------------------
# Views: history
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_history_lists_every_revision_newest_first(client, project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    update_page(
        page=page,
        actor=pm_user,
        title="Protocol",
        content_markdown="v2",
        summary="second pass",
    )
    client.force_login(pm_user)

    content = client.get(_url("history", project, slug=page.slug)).content.decode()

    assert "second pass" in content
    assert "Current" in content
    # Newest first: the second edit's summary comes before the creation's.
    assert content.index("second pass") < content.index("Page created")


@pytest.mark.django_db
def test_a_revision_shows_what_that_edit_changed(client, project, pm_user):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="old line"
    )
    update_page(
        page=page, actor=pm_user, title="Protocol", content_markdown="new line"
    )
    client.force_login(pm_user)

    content = client.get(
        _url("revision", project, slug=page.slug, number=2)
    ).content.decode()

    assert "wiki-diff-added" in content
    assert "wiki-diff-removed" in content
    assert "new line" in content
    assert "old line" in content


@pytest.mark.django_db
def test_restoring_from_the_revision_page_brings_the_text_back(
    client, project, pm_user
):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    update_page(page=page, actor=pm_user, title="Protocol", content_markdown="v2")
    client.force_login(pm_user)

    response = client.post(
        _url("restore_revision", project, slug=page.slug, number=1)
    )

    page.refresh_from_db()
    assert response.status_code == 302
    assert page.content_markdown == "v1"
    assert page.latest_revision_number == 3


@pytest.mark.django_db
def test_a_locked_page_cannot_be_restored_by_a_contributor(
    client, project, pm_user, contributor
):
    page = create_page(
        project=project, actor=pm_user, title="Protocol", content_markdown="v1"
    )
    update_page(
        page=page, actor=pm_user, title="Protocol", content_markdown="v2", locked=True
    )
    client.force_login(contributor)

    response = client.post(
        _url("restore_revision", project, slug=page.slug, number=1)
    )

    page.refresh_from_db()
    assert response.status_code == 403
    assert page.content_markdown == "v2"


@pytest.mark.django_db
def test_a_revision_number_that_does_not_exist_is_a_404(client, project, pm_user):
    page = create_page(project=project, actor=pm_user, title="Protocol")
    client.force_login(pm_user)

    response = client.get(_url("revision", project, slug=page.slug, number=99))

    assert response.status_code == 404
