"""Custom Django email backends.

Currently exposes :class:`ResendEmailBackend`, a thin adapter around the
``resend`` Python SDK so Django's ``send_mail`` / ``EmailMultiAlternatives``
flow can target Resend via ``EMAIL_BACKEND_CHOICE=resend``.
"""

from __future__ import annotations

from typing import Iterable

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage


class ResendEmailBackend(BaseEmailBackend):
    """Send ``django.core.mail`` messages through Resend."""

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        super().__init__(fail_silently=fail_silently, **kwargs)
        self._api_key = getattr(settings, "RESEND_API_KEY", "")
        self._resend = None

    def _ensure_client(self):
        if self._resend is not None:
            return self._resend
        try:
            import resend  # noqa: WPS433 (runtime import keeps dep optional)
        except ImportError as exc:  # pragma: no cover
            if self.fail_silently:
                return None
            raise RuntimeError(
                "The ``resend`` package is required for ResendEmailBackend."
            ) from exc
        resend.api_key = self._api_key
        self._resend = resend
        return self._resend

    def send_messages(self, email_messages: Iterable[EmailMessage]) -> int:
        if not email_messages:
            return 0
        client = self._ensure_client()
        if client is None:
            return 0
        sent = 0
        for message in email_messages:
            try:
                self._send(client, message)
            except Exception:
                if not self.fail_silently:
                    raise
                continue
            sent += 1
        return sent

    def _send(self, client, message: EmailMessage) -> None:
        payload: dict = {
            "from": message.from_email,
            "to": list(message.to),
            "subject": message.subject,
        }
        if message.cc:
            payload["cc"] = list(message.cc)
        if message.bcc:
            payload["bcc"] = list(message.bcc)
        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)

        # Text body is the default; attach HTML alternative if present.
        html_body = None
        for alt_content, alt_mime in getattr(message, "alternatives", []) or []:
            if alt_mime == "text/html":
                html_body = alt_content
                break

        if message.content_subtype == "html":
            payload["html"] = message.body
        else:
            payload["text"] = message.body
            if html_body is not None:
                payload["html"] = html_body

        client.Emails.send(payload)
