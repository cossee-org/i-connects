from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import PublicProfileView

urlpatterns = [
    # ``apps.core.urls`` is mounted before Django's admin so the custom
    # platform-admin console at ``/admin/console/`` wins over ``/admin/*``.
    path("", include("apps.core.urls")),
    path("admin/", admin.site.urls),
    # Public researcher profile lives at the top level so the URL stays
    # ``/people/<id>/`` rather than ``/accounts/people/<id>/``.
    path(
        "people/<int:pk>/",
        PublicProfileView.as_view(),
        name="public_profile",
    ),
    # Our accounts URLs take precedence over allauth. Allauth's ORCID OAuth
    # routes (/accounts/orcid/login/, /accounts/orcid/callback/) remain
    # reachable because they don't collide with any path we define.
    path("accounts/", include("apps.accounts.urls")),
    path("accounts/", include("allauth.urls")),
    # App URLs (each module uses ``app_name`` so namespace is auto-derived).
    path("", include("apps.projects.urls")),
    path("", include("apps.roles.urls")),
    path("", include("apps.tasks.urls")),
    path("", include("apps.comments.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.notes.urls")),
    path("", include("apps.wiki.urls")),
    path("", include("apps.forms_builder.urls")),
    path("", include("apps.contributions.urls")),
    path("", include("apps.audit.urls")),
    path("", include("apps.mailing_list.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
