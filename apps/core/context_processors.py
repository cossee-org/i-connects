"""Template context for which modules and columns the templates draw.

Each is a setting, off while the feature is hidden for now.
"""

from __future__ import annotations

from django.conf import settings


def module_visibility(request):
    return {
        "show_surveys_and_communications": settings.SHOW_SURVEYS_AND_COMMUNICATIONS,
        "show_member_roles": settings.SHOW_MEMBER_ROLES,
    }
