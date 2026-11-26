"""Every email uses the shared template, and only the shared template styles.

These tests render every email in ``templates/emails/`` with the made-up
context in ``apps.core.email_samples`` and check the parts every email must
have, so a new email that skips the base, or styles itself, fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings

from apps.core.email_samples import email_names, sample_context
from apps.core.emails import render_email

EMAILS_DIR = Path(settings.BASE_DIR) / "templates" / "emails"
# The only email templates allowed to carry styles or colours.
STYLED = {EMAILS_DIR / "base_email.html"} | set((EMAILS_DIR / "_parts").glob("*.html"))


def _email_folders() -> list[str]:
    return sorted(
        p.name for p in EMAILS_DIR.iterdir() if p.is_dir() and p.name not in {"_parts", "layouts"}
    )


def test_every_email_folder_has_a_sample_and_all_three_templates():
    assert sorted(email_names()) == _email_folders()
    for name in _email_folders():
        for part in ("subject.txt", "body.txt", "body.html"):
            assert (EMAILS_DIR / name / part).is_file(), f"{name}/{part} is missing"


@pytest.mark.parametrize("name", email_names())
@override_settings(SITE_URL="https://iconnects.test")
def test_the_html_part_uses_the_shared_layout(name):
    html = render_email(name, sample_context(name)).html

    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert 'src="https://iconnects.test/static/img/email/logo@2x.png"' in html
    assert "border-bottom:3px solid #f03d30" in html  # the coral rule under the logo

    preheader = re.search(r"mso-hide:all;\">(.*?)&#8199;", html, re.S).group(1)
    assert preheader.strip(), f"{name} has no preheader"
    heading = re.search(r"<h1 [^>]*>(.*?)</h1>", html, re.S).group(1)
    assert heading.strip(), f"{name} has no heading"
    footer = re.search(r"color:#5f6380;text-align:left;\">(.*?)<br>", html, re.S).group(1)
    assert "You got this email" in footer, f"{name} does not say why it was sent"


@pytest.mark.parametrize("name", email_names())
@override_settings(SITE_URL="https://iconnects.test")
def test_the_text_part_has_a_heading_and_the_footer(name):
    rendered = render_email(name, sample_context(name))
    text = rendered.text

    assert rendered.subject and "\n" not in rendered.subject
    assert text.strip().splitlines()[0], f"{name} text part starts blank"
    body, _, footer = text.rpartition("\n--\n")
    assert body, f"{name} text part has no footer separator"
    assert "You got this email" in footer
    assert footer.rstrip().endswith("i-CONNECTS: https://iconnects.test/")
    assert "\n\n\n" not in text
    assert "&amp;" not in text and "&#" not in text  # plain text is not escaped


@pytest.mark.parametrize("name", email_names())
def test_the_email_has_one_call_to_action_at_most(name):
    html = render_email(name, sample_context(name)).html
    assert html.count("background-color:#d93226") <= 1


def test_only_the_base_and_the_parts_carry_styles():
    offenders = []
    for path in sorted(EMAILS_DIR.rglob("*.html")):
        if path in STYLED:
            continue
        source = path.read_text()
        if "style=" in source or "<style" in source or re.search(r"#[0-9a-fA-F]{3,6}\b", source):
            offenders.append(str(path.relative_to(EMAILS_DIR)))
    assert offenders == [], (
        "Email templates must not style themselves; use a part from "
        f"templates/emails/_parts/ instead: {offenders}"
    )


@pytest.mark.django_db
def test_the_preview_renders_each_email_and_its_text():
    from apps.core.views import email_preview

    from django.contrib.auth.models import AnonymousUser

    factory = RequestFactory()
    request = factory.get("/dev/emails/")
    request.user = AnonymousUser()
    index = email_preview(request)
    assert index.status_code == 200
    html = email_preview(factory.get("/dev/emails/digest/"), name="digest")
    assert b"Your daily digest" in html.content
    text = email_preview(factory.get("/dev/emails/digest/?format=text"), name="digest")
    assert text["Content-Type"].startswith("text/plain")
    assert text.content.startswith(b"Subject: ")
