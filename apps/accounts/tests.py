"""Smoke tests for the accounts app."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import Widget


@pytest.mark.django_db
def test_signup_creates_user_and_logs_in(client):
    """POST /accounts/signup/ creates the user and logs them in."""
    url = reverse("accounts:signup")
    response = client.post(
        url,
        {
            "given_name": "New",
            "family_name": "Bie",
            "email": "newbie@test.com",
            "email_confirm": "newbie@test.com",
            "password": "supersecure123",
            "password_confirm": "supersecure123",
            "agree_terms": "on",
        },
    )
    assert response.status_code in (301, 302)
    User = get_user_model()
    assert User.objects.filter(email="newbie@test.com").exists()
    # Session should be authenticated.
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_signup_creates_default_widgets(client):
    """The signup post_save signal seeds the 10-widget dashboard defaults."""
    client.post(
        reverse("accounts:signup"),
        {
            "given_name": "",
            "family_name": "Widgets",
            "email": "widgets@test.com",
            "email_confirm": "widgets@test.com",
            "password": "supersecure123",
            "password_confirm": "supersecure123",
            "agree_terms": "on",
        },
    )
    User = get_user_model()
    user = User.objects.get(email="widgets@test.com")
    assert Widget.objects.filter(user=user).count() == 10


@pytest.mark.django_db
def test_login_valid_credentials(client, user):
    response = client.post(
        reverse("accounts:login"),
        {"email": user.email, "password": "password123"},
    )
    assert response.status_code in (301, 302)
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_login_invalid_credentials(client, user):
    response = client.post(
        reverse("accounts:login"),
        {"email": user.email, "password": "wrong-password"},
    )
    assert response.status_code == 200
    # Session should NOT have an authenticated user.
    assert "_auth_user_id" not in client.session
    # Form should surface an error.
    content = response.content.decode()
    assert "Invalid email or password" in content or "error" in content.lower()


@pytest.mark.django_db
def test_logout_requires_post(client_logged_in):
    url = reverse("accounts:logout")
    # GET is not allowed.
    get_response = client_logged_in.get(url)
    assert get_response.status_code == 405
    # POST actually logs the user out.
    post_response = client_logged_in.post(url)
    assert post_response.status_code in (301, 302)
    assert "_auth_user_id" not in client_logged_in.session


@pytest.mark.django_db
def test_profile_requires_login(client):
    response = client.get(reverse("accounts:profile"))
    # Unauthenticated -> redirected to login.
    assert response.status_code in (301, 302)
    assert "/accounts/login" in response["Location"]
