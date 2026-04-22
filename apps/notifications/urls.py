"""URL configuration for the notifications app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("notifications/", views.notification_list, name="list"),
    path("notifications/badge/", views.notification_badge, name="badge"),
    path("notifications/dropdown/", views.notification_dropdown, name="dropdown"),
    path("notifications/<int:pk>/read/", views.mark_read, name="mark_read"),
    path("notifications/read-all/", views.mark_all_read, name="mark_all_read"),
    path(
        "notifications/announcements/new/",
        views.announcement_create,
        name="announcement_create",
    ),
]
