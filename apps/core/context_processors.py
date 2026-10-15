"""Template context for which modules ``base.html`` draws in its navigation."""

from __future__ import annotations

from django.conf import settings


def module_visibility(request):
    return {
        "show_surveys_and_communications": settings.SHOW_SURVEYS_AND_COMMUNICATIONS,
    }
