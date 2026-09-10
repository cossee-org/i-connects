"""URL patterns for the accounts app.

The include point is wired up in Phase 5; this module stays self-contained
so the namespace and names are stable across phases.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "confirm/sent/",
        views.confirmation_sent,
        name="confirmation_sent",
    ),
    path(
        "confirm/resend/",
        views.ResendConfirmationView.as_view(),
        name="resend_confirmation",
    ),
    path(
        "confirm/<uidb64>/<token>/",
        views.email_confirm,
        name="email_confirm",
    ),
    path(
        "password/forgot/",
        views.PasswordResetRequestView.as_view(),
        name="password_reset_request",
    ),
    path(
        "password/reset/sent/",
        views.password_reset_sent,
        name="password_reset_sent",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/done/",
        views.password_reset_done,
        name="password_reset_done",
    ),
    path(
        "password/change/",
        views.PasswordChangeView.as_view(),
        name="password_change",
    ),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("people/", views.DiscoverPeopleView.as_view(), name="discover_people"),
    path(
        "lockout/unlock/<str:token>/",
        views.lockout_unlock,
        name="lockout_unlock",
    ),
]
