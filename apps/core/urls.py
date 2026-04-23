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
]
