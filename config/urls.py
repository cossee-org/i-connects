from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Landing page at /
    path("", include("apps.core.urls")),
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
    path("", include("apps.forms_builder.urls")),
    path("", include("apps.contributions.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
