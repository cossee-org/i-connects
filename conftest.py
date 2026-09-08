"""Shared pytest fixtures for the i-connects test suite."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="alice@test.com",
        password="password123",
        name="Alice",
        email_confirmed_at=timezone.now(),
    )


@pytest.fixture
def pm_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="pm@test.com",
        password="password123",
        name="PM",
        email_confirmed_at=timezone.now(),
    )


@pytest.fixture
def other_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="bob@test.com",
        password="password123",
        name="Bob",
        email_confirmed_at=timezone.now(),
    )


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client
