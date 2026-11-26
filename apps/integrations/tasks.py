"""Celery tasks for outbound integrations."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="integrations.post_zulip_message", ignore_result=True)
def post_zulip_message(connection_id: int, content: str) -> bool:
    """Send one message to a project's Zulip topic.

    Reads the connection when it runs, so a project disconnected (or
    paused) between the event and the send stays quiet.
    """
    from .models import ZulipConnection
    from .zulip import deliver

    connection = ZulipConnection.objects.filter(pk=connection_id, enabled=True).first()
    if connection is None:
        return False
    ok, detail = deliver(connection, content)
    if not ok:
        logger.warning(
            "zulip message for project %s not delivered: %s",
            connection.project_id,
            detail,
        )
    return ok
