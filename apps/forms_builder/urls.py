from django.urls import path

from . import views

app_name = "forms_builder"

urlpatterns = [
    # Form templates
    path("forms/", views.form_list, name="form_list"),
    path("forms/new/", views.form_create, name="form_create"),
    path("forms/<int:pk>/edit/", views.form_update, name="form_update"),
    path("forms/<int:pk>/delete/", views.form_delete, name="form_delete"),

    # Versions
    path("forms/<int:pk>/versions/", views.version_list, name="version_list"),
    path(
        "forms/<int:pk>/versions/new/",
        views.version_create,
        name="version_create",
    ),
    path(
        "forms/<int:pk>/versions/<int:vpk>/",
        views.version_detail,
        name="version_detail",
    ),
    path(
        "forms/<int:pk>/versions/<int:vpk>/clone/",
        views.version_clone,
        name="version_clone",
    ),
    path(
        "forms/<int:pk>/versions/<int:vpk>/archive/",
        views.version_archive,
        name="version_archive",
    ),

    # Attach + fill
    path(
        "projects/<int:project_pk>/forms/",
        views.project_forms,
        name="project_forms",
    ),
    path(
        "projects/<int:project_pk>/forms/fill/",
        views.project_fill,
        name="project_fill",
    ),
    path(
        "tasks/<int:task_pk>/forms/fill/",
        views.task_fill,
        name="task_fill",
    ),
]
