"""Tests for the outbound Discord webhook integration."""

from __future__ import annotations

import pytest

from apps.core.enums import ContributionType, ProjectStatus
from apps.integrations import discord as discord_mod
from apps.integrations.discord import (
    announce_task_approved,
    is_valid_webhook_url,
    post_webhook,
)
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task


def test_is_valid_webhook_url_accepts_discord_com():
    assert is_valid_webhook_url("https://discord.com/api/webhooks/123/abc") is True
    assert is_valid_webhook_url("https://discordapp.com/api/webhooks/123/abc") is True


def test_is_valid_webhook_url_rejects_non_discord():
    # SSRF protection: arbitrary hosts must be refused.
    assert is_valid_webhook_url("https://evil.com/api/webhooks/123/abc") is False
    assert is_valid_webhook_url("https://discord.com.evil.com/x") is False


def test_is_valid_webhook_url_rejects_http():
    assert is_valid_webhook_url("http://discord.com/api/webhooks/123/abc") is False


def test_post_webhook_skips_invalid_url():
    # Garbage URL returns None without raising or hitting the network.
    assert post_webhook("not-a-url", payload={"x": 1}) is None
    assert post_webhook("", payload={"x": 1}) is None


@pytest.mark.django_db
def test_announce_task_approved_noop_when_webhook_empty(user):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = project.project_members.first()
    container = KanbanBoard.objects.create(
        project=project, name="To Do", container_order=0
    )
    task = Task.objects.create(
        project=project,
        container=container,
        container_task_order=0,
        name="Do thing",
        created_by=member,
        contribution_type=ContributionType.WRITING_ORIGINAL_DRAFT,
    )
    # Project has no webhook configured -> no-op, no network call.
    assert announce_task_approved(task, verifier=user) is None


@pytest.mark.django_db
def test_announce_task_approved_calls_post_webhook_when_configured(
    user, monkeypatch
):
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P2", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    project.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    project.save(update_fields=["discord_webhook_url"])

    member = project.project_members.first()
    container = KanbanBoard.objects.create(
        project=project, name="To Do", container_order=0
    )
    task = Task.objects.create(
        project=project,
        container=container,
        container_task_order=0,
        name="Draft manuscript",
        created_by=member,
        contribution_type=ContributionType.WRITING_ORIGINAL_DRAFT,
    )

    captured: dict = {}

    def fake_post_webhook(url, *, payload, timeout=5.0):
        captured["url"] = url
        captured["payload"] = payload
        return 204

    monkeypatch.setattr(discord_mod, "post_webhook", fake_post_webhook)

    result = announce_task_approved(task, verifier=user)

    assert result == 204
    assert captured["url"] == "https://discord.com/api/webhooks/1/abc"
    embed = captured["payload"]["embeds"][0]
    assert embed["title"] == "Task approved: Draft manuscript"
    # Project name appears as a field value.
    field_values = {f["name"]: f["value"] for f in embed["fields"]}
    assert field_values["Project"] == "P2"
