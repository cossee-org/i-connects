"""Shared pytest fixtures for the i-connects test suite."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="alice@test.com", password="password123", name="Alice"
    )


@pytest.fixture
def pm_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="pm@test.com", password="password123", name="PM"
    )


@pytest.fixture
def other_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="bob@test.com", password="password123", name="Bob"
    )


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client
