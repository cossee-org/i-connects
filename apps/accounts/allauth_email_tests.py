"""django-allauth's account emails go out in the i-CONNECTS email template.

allauth sends its own emails for ORCID sign-up confirmation, adding an
address and its password reset pages. ``AccountAdapter.send_mail`` routes
them through ``apps.core.emails.send_email``; these tests drive the real
allauth flows and check what leaves.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.test import RequestFactory


@pytest.fixture(autouse=True)
def _site_url(settings):
    settings.SITE_URL = "https://iconnects.test"


def _assert_branded(message):
    html = message.alternatives[0][0]
    assert "img/email/logo@2x.png" in html
    assert "border-bottom:3px solid #f03d30" in html
    assert "You got this email about your i-CONNECTS account." in html
    assert "example.com" not in message.subject + message.body + html
    assert not message.subject.startswith("[")


@pytest.mark.django_db
def test_allauth_password_reset_uses_the_shared_template(client, user):
    client.post("/accounts/password/reset/", {"email": user.email})

    [message] = mail.outbox
    _assert_branded(message)
    assert message.subject == "Reset your i-CONNECTS password"
    assert "/accounts/password/reset/key/" in message.body


@pytest.mark.django_db
def test_a_reset_for_an_unknown_address_uses_the_shared_template(client):
    client.post("/accounts/password/reset/", {"email": "nobody@test.com"})

    [message] = mail.outbox
    _assert_branded(message)
    assert message.to == ["nobody@test.com"]
    assert "nobody@test.com" in message.body
    assert "/accounts/signup/" in message.body


def _request_for(user):
    request = RequestFactory().get("/")
    request.user = user
    SessionMiddleware(lambda r: None).process_request(request)
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
def test_the_sign_up_confirmation_uses_the_shared_template(user):
    from allauth.account.utils import send_email_confirmation

    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=False)
    send_email_confirmation(_request_for(user), user, signup=True)

    [message] = mail.outbox
    _assert_branded(message)
    assert message.subject == "Confirm your i-CONNECTS email address"
    assert "Thanks for signing up" in message.body
    assert "/accounts/confirm-email/" in message.body


@pytest.mark.django_db
def test_adding_an_address_sends_the_shared_confirmation(client, user):
    client.force_login(user)
    client.post("/accounts/email/", {"action_add": "", "email": "second@test.com"})

    [message] = mail.outbox
    _assert_branded(message)
    assert message.to == ["second@test.com"]
    assert "add it to your i-CONNECTS account" in message.body


@pytest.mark.django_db
def test_an_allauth_email_without_a_template_still_gets_the_layout(user, caplog):
    from apps.accounts.adapters import AccountAdapter

    request = _request_for(user)
    from allauth.core import context

    with context.request_context(request):
        AccountAdapter(request).send_mail(
            "account/email/password_changed",
            user.email,
            {"user": user, "timestamp": None, "ip": "", "user_agent": ""},
        )

    [message] = mail.outbox
    html = message.alternatives[0][0]
    assert "img/email/logo@2x.png" in html
    assert "has no i-CONNECTS template" in caplog.text
