"""Who may comment on a task, and tagging people (@Name) in a comment."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.comments.models import Comment, CommentMention
from apps.comments.services import can_comment, mentionable_users
from apps.comments.tasks import send_comment_mention_email_task
from apps.core.enums import MemberPrivileges, ProjectStatus
from apps.notifications.models import Notification
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task


@pytest.fixture(autouse=True)
def _site_url(settings):
    settings.SITE_URL = "https://iconnects.test"


def _user(email, given, family=""):
    return get_user_model().objects.create_user(
        email=email,
        password="password123",
        name=given,
        given_name=given,
        family_name=family,
        email_confirmed_at=timezone.now(),
    )


def _join(project, user, privilege=MemberPrivileges.CONTRIBUTOR):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(project=project, user=user, privilege=privilege)
    return member


class Cast:
    """A task and everyone around it.

    ``creator`` is a Lead who made the task. ``assignee``, ``reviewer`` and
    ``bystander`` are plain members; only the first two are on the task.
    ``lead`` is a second Lead who is not on it. ``outsider`` is in no project.
    """

    def __init__(self, creator):
        self.creator = creator
        self.project = create_project_with_owner(
            user=creator,
            form_data={"name": "Reefs", "status": ProjectStatus.ACTIVE, "is_public": True},
        )
        self.creator_member = ProjectMember.objects.get(project=self.project, users=creator)
        column = KanbanBoard.objects.filter(project=self.project).first()
        self.task = Task.objects.create(
            project=self.project,
            created_by=self.creator_member,
            container=column,
            container_task_order=0,
            name="Count corals",
        )
        self.assignee = _user("asha@test.com", "Asha", "Rao")
        self.assignee_member = _join(self.project, self.assignee)
        self.reviewer = _user("ravi@test.com", "Ravi", "Shah")
        self.reviewer_member = _join(self.project, self.reviewer)
        self.bystander = _user("bea@test.com", "Bea", "Lund")
        self.bystander_member = _join(self.project, self.bystander)
        self.lead = _user("lena@test.com", "Lena", "Park")
        self.lead_member = _join(self.project, self.lead, MemberPrivileges.PROJECT_MANAGER)
        self.outsider = _user("otto@test.com", "Otto", "Berg")
        self.task.assigned_members.add(self.assignee_member)
        self.task.reviewers.add(self.reviewer_member)

    def create_url(self):
        return reverse("comments:create", kwargs={"task_pk": self.task.pk})

    def pane_url(self):
        return reverse(
            "tasks:detail_pane", kwargs={"project_pk": self.project.pk, "pk": self.task.pk}
        )

    def detail_url(self):
        return reverse(
            "tasks:detail", kwargs={"project_pk": self.project.pk, "pk": self.task.pk}
        )


@pytest.fixture
def cast(db, user):
    return Cast(user)


def _post(client, cast, as_user, content, mentioned=()):
    client.force_login(as_user)
    return client.post(
        cast.create_url(),
        {"content": content, "mentioned_user_ids": [u.pk for u in mentioned]},
    )


# ---------------------------------------------------------------------------
# Who may comment
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("who", ["creator", "assignee", "reviewer"])
def test_participants_can_comment(client, cast, who):
    response = _post(client, cast, getattr(cast, who), "On it")
    assert response.status_code == 200
    assert cast.task.comments.filter(content="On it").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["bystander", "lead"])
def test_others_in_the_project_cannot(client, cast, who):
    """A plain member, and a Lead who is not on the task, can only read."""
    response = _post(client, cast, getattr(cast, who), "Me too")
    assert response.status_code == 403
    assert not cast.task.comments.exists()


@pytest.mark.django_db
def test_a_watcher_is_not_a_participant(client, cast):
    cast.task.watchers.add(cast.bystander_member)
    assert _post(client, cast, cast.bystander, "Watching").status_code == 403


@pytest.mark.django_db
def test_a_team_on_the_task_counts_for_each_of_its_users(cast):
    team = ProjectMember.objects.create(project=cast.project, name="Divers")
    team.users.add(cast.bystander)
    assert not can_comment(cast.bystander, cast.task)
    cast.task.assigned_members.add(team)
    assert can_comment(cast.bystander, cast.task)


@pytest.mark.django_db
def test_a_membership_that_left_no_longer_counts(cast):
    cast.assignee_member.deleted = True
    cast.assignee_member.save()
    assert not can_comment(cast.assignee, cast.task)


@pytest.mark.django_db
def test_the_box_is_drawn_only_for_participants(client, cast):
    box = f'hx-post="{cast.create_url()}"'
    note = "Only the task&#x27;s creator, assignees and reviewers can comment."
    for url in (cast.pane_url(), cast.detail_url()):
        client.force_login(cast.assignee)
        html = client.get(url).content.decode()
        assert box in html and note not in html

        client.force_login(cast.bystander)
        html = client.get(url).content.decode()
        assert box not in html
        assert "creator, assignees and reviewers can comment" in html


@pytest.mark.django_db
def test_an_author_taken_off_the_task_cannot_edit_but_can_delete(client, cast):
    comment = Comment.objects.create(task=cast.task, author=cast.assignee_member, content="x")
    cast.task.assigned_members.remove(cast.assignee_member)
    client.force_login(cast.assignee)

    edit_url = reverse("comments:edit", kwargs={"pk": comment.pk})
    assert client.get(edit_url).status_code == 403
    assert client.post(edit_url, {"content": "y"}).status_code == 403
    comment.refresh_from_db()
    assert comment.content == "x"

    response = client.post(reverse("comments:delete", kwargs={"pk": comment.pk}))
    assert response.status_code == 204
    assert not Comment.objects.filter(pk=comment.pk).exists()


# ---------------------------------------------------------------------------
# Who may be tagged
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_anyone_who_can_open_the_task_can_be_tagged(cast):
    tagged = set(mentionable_users(cast.project))
    assert {cast.creator, cast.assignee, cast.reviewer, cast.bystander, cast.lead} <= tagged
    assert cast.outsider not in tagged


@pytest.mark.django_db
def test_people_who_left_or_are_inactive_cannot_be_tagged(cast):
    cast.bystander_member.deleted = True
    cast.bystander_member.save()
    cast.reviewer.is_active = False
    cast.reviewer.save()
    tagged = set(mentionable_users(cast.project))
    assert cast.bystander not in tagged
    assert cast.reviewer not in tagged


@pytest.mark.django_db
def test_the_at_list_names_but_does_not_email(client, cast):
    """The page carries names and ids for the @ list, never addresses, and
    leaves out the person writing."""
    client.force_login(cast.assignee)
    html = client.get(cast.pane_url()).content.decode()
    assert '"name": "Bea Lund"' in html
    assert "bea@test.com" not in html
    assert '"name": "Asha Rao"' not in html


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_tag_is_saved_and_highlighted(client, cast, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, cast, cast.assignee, "@Bea Lund can you check?", [cast.bystander])
    assert response.status_code == 200
    comment = cast.task.comments.get()
    assert list(comment.mentions.values_list("user_id", flat=True)) == [cast.bystander.pk]
    html = response.content.decode()
    assert '<span class="comment-mention font-medium text-brand-navy">@Bea Lund</span> can you check?' in html


@pytest.mark.django_db
def test_a_tag_whose_name_was_deleted_from_the_text_is_dropped(client, cast):
    _post(client, cast, cast.assignee, "never mind", [cast.bystander])
    assert not CommentMention.objects.exists()


@pytest.mark.django_db
def test_a_prefix_of_a_longer_word_is_not_a_tag(client, cast):
    """Picking "Bea Lund" and then typing "@Bea Lundqvist" tags no one."""
    _post(client, cast, cast.assignee, "@Bea Lundqvist", [cast.bystander])
    assert not CommentMention.objects.exists()


@pytest.mark.django_db
def test_outsiders_bad_ids_and_yourself_are_dropped_not_refused(client, cast):
    client.force_login(cast.assignee)
    response = client.post(
        cast.create_url(),
        {
            "content": "@Otto Berg @Asha Rao @Ravi Shah",
            "mentioned_user_ids": [
                cast.outsider.pk,
                "nonsense",
                cast.assignee.pk,
                cast.reviewer.pk,
            ],
        },
    )
    assert response.status_code == 200
    comment = cast.task.comments.get()
    assert set(comment.mentions.values_list("user_id", flat=True)) == {cast.reviewer.pk}

    client.post(
        reverse("comments:edit", kwargs={"pk": comment.pk}),
        {"content": "@Asha Rao", "mentioned_user_ids": [cast.assignee.pk]},
    )
    assert not comment.mentions.exists()


@pytest.mark.django_db
def test_a_name_typed_by_hand_is_not_highlighted(cast):
    """Only tagged people are marked up, and the text stays escaped."""
    comment = Comment.objects.create(
        task=cast.task, author=cast.assignee_member, content="@Bea Lund <b>hi</b>"
    )
    html = render_to_string("comments/comment_item.html", {"comment": comment, "task": cast.task})
    assert "comment-mention" not in html
    assert "&lt;b&gt;hi&lt;/b&gt;" in html


# ---------------------------------------------------------------------------
# Telling them
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_tagged_person_is_notified_and_emailed(client, cast, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        _post(client, cast, cast.assignee, "@Lena Park please <b>approve</b>", [cast.lead])

    note = Notification.objects.for_user(cast.lead).get()
    assert note.headline == "Asha Rao mentioned you on Count corals"
    assert note.url == cast.detail_url()

    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert email.to == ["lena@test.com"]
    assert email.subject == 'i-connects: Asha Rao mentioned you on "Count corals"'
    assert "https://iconnects.test" + cast.detail_url() in email.body
    assert "@Lena Park please <b>approve</b>" in email.body  # plain text
    html = email.alternatives[0][0]
    assert "&lt;b&gt;approve&lt;/b&gt;" in html
    assert "<b>approve</b>" not in html


@pytest.mark.django_db
def test_turning_the_email_off_keeps_the_notification(client, cast, django_capture_on_commit_callbacks):
    cast.lead.email_comment_mention = False
    cast.lead.save()
    with django_capture_on_commit_callbacks(execute=True):
        _post(client, cast, cast.assignee, "@Lena Park", [cast.lead])
    assert Notification.objects.for_user(cast.lead).exists()
    assert mail.outbox == []


@pytest.mark.django_db
def test_an_edit_emails_only_the_people_it_adds(client, cast, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        _post(client, cast, cast.assignee, "@Lena Park", [cast.lead])
    comment = cast.task.comments.get()
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("comments:edit", kwargs={"pk": comment.pk}),
            {
                "content": "@Lena Park and @Bea Lund",
                "mentioned_user_ids": [cast.lead.pk, cast.bystander.pk],
            },
        )
    assert response.status_code == 200
    assert [m.to for m in mail.outbox] == [["bea@test.com"]]
    assert set(comment.mentions.values_list("user_id", flat=True)) == {
        cast.lead.pk,
        cast.bystander.pk,
    }

    # Taking a tag out removes it, and tells no one.
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("comments:edit", kwargs={"pk": comment.pk}),
            {"content": "@Bea Lund", "mentioned_user_ids": [cast.bystander.pk]},
        )
    assert mail.outbox == []
    assert list(comment.mentions.values_list("user_id", flat=True)) == [cast.bystander.pk]


@pytest.mark.django_db
def test_the_email_rechecks_everything_when_it_runs(cast):
    comment = Comment.objects.create(task=cast.task, author=cast.assignee_member, content="@Bea Lund")
    CommentMention.objects.create(comment=comment, user=cast.bystander)

    # Left the project before the worker got to it.
    cast.bystander_member.deleted = True
    cast.bystander_member.save()
    send_comment_mention_email_task(comment.pk, [cast.bystander.pk])
    assert mail.outbox == []

    # Tag edited out.
    cast.bystander_member.deleted = False
    cast.bystander_member.save()
    send_comment_mention_email_task(comment.pk, [cast.lead.pk])
    assert mail.outbox == []

    # Comment deleted.
    comment_pk = comment.pk
    comment.delete()
    send_comment_mention_email_task(comment_pk, [cast.bystander.pk])
    assert mail.outbox == []


@pytest.mark.django_db
def test_the_preference_is_on_the_profile_page(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "Email me when I&#x27;m mentioned in a comment" in html
