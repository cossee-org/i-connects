"""The confirmation before unticking Done, Reviewed or Verified.

A ticked box that may be unticked no longer posts on click: it asks the
withdraw dialog (task_withdraw_confirm_modal.html), which says what the
untick undoes — built per row by ``views._withdraw_prompts`` — and posts
the same request the untick used to once confirmed.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from apps.projects.models import ContributionTracking
from apps.tasks.deadline_tests import _setup
from apps.tasks.services import (
    Attachments,
    close_task,
    mark_own_done,
    mark_reviewed,
    verify_part,
)


def _block(client, task):
    return client.get(
        reverse("tasks:status_block", kwargs={"pk": task.pk}) + "?pane=1"
    ).content.decode()


def _prompt(html, member, kind):
    """The JSON prompt the ``kind`` box in ``member``'s row hands the dialog."""
    row = html[html.index(f'data-assignee-row="{member.pk}"') :]
    row = row[: row.index("</tr>")]
    box = row.index(f'data-confirm-withdraw="{kind}"')
    script = row.rindex("<script", 0, box)
    return json.loads(re.search(r">(.*?)</script>", row[script:], re.S).group(1))


@pytest.fixture
def marked(pm_user, user, other_user):
    """Bob's part is done with attachments, reviewed by Carol (a reviewer)
    and verified by the Lead, on a CRediT project."""
    project, task, lead, [bob, carol] = _setup(pm_user, user, other_user, deadline_days=10)
    project.contribution_tracking = ContributionTracking.CREDIT
    project.save()
    task.reviewers.add(carol)
    log = mark_own_done(task=task, member=bob, attachments=Attachments(urls=["https://example.com"]))
    mark_reviewed(task_log=log, reviewer=carol)
    verify_part(task_log=log, lead=lead)
    return project, task, lead, bob, carol


@pytest.mark.django_db
def test_ticked_boxes_ask_before_unticking(client, pm_user, marked):
    _project, task, _lead, bob, _carol = marked
    client.force_login(pm_user)
    html = _block(client, task)

    row = html[html.index(f'data-assignee-row="{bob.pk}"') :]
    row = row[: row.index("</tr>")]
    for kind in ("done", "review", "verify"):
        assert f'data-confirm-withdraw="{kind}"' in row
    # None of the three posts on click any more.
    assert "hx-post" not in row
    assert "data-withdraw-modal" in html


@pytest.mark.django_db
def test_undoing_done_names_what_goes_with_it(client, pm_user, marked):
    _project, task, lead, bob, carol = marked
    client.force_login(pm_user)

    prompt = _prompt(_block(client, task), bob, "done")

    assert prompt["title"] == f"Mark {bob.display_name}'s part not done?"
    assert prompt["confirm"] == "Mark not done"
    assert prompt["lines"][0] == f"The review by {carol.display_name} is cleared."
    assert prompt["lines"][1].startswith("The verification by you is cleared, and the credit")
    assert f"gave {bob.display_name} as Other comes off Contribution Tracking" in prompt["lines"][1]
    assert "Their attachments stay." in prompt["lines"]


@pytest.mark.django_db
def test_the_assignee_hears_it_in_the_second_person(client, user, marked):
    _project, task, _lead, bob, _carol = marked
    client.force_login(bob.users.first())

    prompt = _prompt(_block(client, task), bob, "done")

    assert prompt["title"] == "Mark your part not done?"
    assert "Your attachments stay, and you can change them again." in prompt["lines"]
    assert "gave you as Other" in prompt["lines"][1]


@pytest.mark.django_db
def test_withdrawing_a_review_or_verification(client, pm_user, other_user, marked):
    _project, task, _lead, bob, carol = marked
    client.force_login(pm_user)
    html = _block(client, task)

    review = _prompt(html, bob, "review")
    assert review["title"] == f"Withdraw {carol.display_name}'s review of {bob.display_name}'s part?"
    assert "The Done mark and any verification stay" in review["lines"][-1]

    verify = _prompt(html, bob, "verify")
    assert verify["title"] == f"Withdraw the verification of {bob.display_name}'s contribution?"
    assert verify["lines"][0].startswith("The credit it gave")

    # The reviewer withdrawing their own review.
    client.force_login(other_user)
    review = _prompt(_block(client, task), bob, "review")
    assert review["title"] == f"Withdraw your review of {bob.display_name}'s part?"


@pytest.mark.django_db
def test_only_what_applies_is_listed(client, pm_user, user):
    """A part done but neither reviewed nor verified, with no attachments, on
    a project that does not track CRediT."""
    _project, task, _lead, [bob] = _setup(pm_user, user, deadline_days=10)
    mark_own_done(task=task, member=bob)
    client.force_login(pm_user)

    prompt = _prompt(_block(client, task), bob, "done")

    assert prompt["lines"] == ["It can be marked done again at any time."]


@pytest.mark.django_db
def test_non_credit_projects_lose_only_the_sign_off(client, pm_user, user):
    _project, task, lead, [bob] = _setup(pm_user, user, deadline_days=10)
    verify_part(task_log=mark_own_done(task=task, member=bob), lead=lead)
    client.force_login(pm_user)

    prompt = _prompt(_block(client, task), bob, "verify")

    assert prompt["lines"][0] == (
        "This project doesn't track CRediT, so only the sign-off is removed."
    )


@pytest.mark.django_db
def test_locked_boxes_never_ask(client, pm_user, user):
    """On a closed task the Lead's marks for others freeze: the box shows the
    locked notice, not the confirmation."""
    _project, task, lead, [bob] = _setup(pm_user, user, deadline_days=10)
    mark_own_done(task=task, member=bob, actor=lead)
    close_task(task=task, closed_by=lead)
    client.force_login(pm_user)

    html = _block(client, task)
    row = html[html.index(f'data-assignee-row="{bob.pk}"') :]
    row = row[: row.index("</tr>")]
    done_cell = row[row.index("data-done-cell") : row.index("data-verify-cell")]
    assert 'data-confirm-withdraw="done"' not in done_cell
    assert "showLocked()" in done_cell


@pytest.mark.django_db
def test_confirming_posts_what_the_untick_did(client, pm_user, marked):
    """The dialog posts the same request as before; the endpoints are
    unchanged."""
    _project, task, _lead, bob, _carol = marked
    client.force_login(pm_user)

    response = client.post(
        reverse("tasks:member_done", kwargs={"pk": task.pk, "member_pk": bob.pk}),
        {"done": "0", "pane_mode": "1"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    log = task.task_logs.filter(assigned_to=bob).first()
    assert log.reviewed_by is None and log.verified_by is None


@pytest.mark.django_db
def test_the_table_turns_into_cards_when_narrow(client, pm_user, marked):
    """The table measures its own width: under 44rem each person becomes a
    card (the same rows, laid out by CSS), with each stage named beside its
    tick; the table's short words and the cards' long ones are both there,
    and "by …" sits on its own line in the table."""
    _project, task, _lead, bob, _carol = marked
    client.force_login(pm_user)
    html = _block(client, task)

    assert ".completion-wrap { container-type: inline-size; }" in html
    assert "@container (max-width: 44rem)" in html
    row = html[html.index(f'data-assignee-row="{bob.pk}"') :]
    row = row[: row.index("</tr>")]
    for label in ("Done", "Reviewed", "Verified"):
        assert f'data-label="{label}"' in row
    assert 'class="completion-by' in row
    # Bob marked his own part: his name is left out of the table's Done cell
    # (shown again in the card and the details row).
    assert "completion-by-self" in row



@pytest.mark.django_db
def test_waiting_cells_carry_short_and_long_words(client, pm_user, user):
    _project, task, _lead, [bob] = _setup(pm_user, user, deadline_days=10)
    mark_own_done(task=task, member=bob)
    client.force_login(pm_user)
    html = _block(client, task)

    row = html[html.index(f'data-assignee-row="{bob.pk}"') :]
    row = row[: row.index("</tr>")]
    assert '<span class="cs-short">Awaiting</span><span class="cs-long">Awaiting verification</span>' in row
