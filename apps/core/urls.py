from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path(
        "admin/console/",
        views.AdminConsoleView.as_view(),
        name="admin_console",
    ),
    path(
        "terms-of-use/",
        views.TermsOfUseView.as_view(),
        name="terms_of_use",
    ),
    path(
        "privacy-policy/",
        views.PrivacyPolicyView.as_view(),
        name="privacy_policy",
    ),
]
