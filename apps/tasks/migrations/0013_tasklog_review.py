"""Add TaskLog.reviewed_by / reviewed_at and the index over the reviewer.

The three schema operations are applied only where the database does not
already carry them. Databases that ran an earlier, locally generated version
of this migration ended up with the columns in place but no ``django_migrations``
row naming *this* file, so a plain ``AddField`` replay failed with
``column "reviewed_at" of relation "tasks_tasklog" already exists`` and left
``migrate`` unable to move past this point. Skipping what is already there
brings those databases back in line without touching the recorded review data,
and is a no-op on a fresh one, which reaches each operation with nothing to skip.

Model state is unaffected: the operations below declare the same fields and
index the autodetector generated, so ``makemigrations`` still sees the app as
up to date. Only the SQL side is conditional.
"""

import django.db.models.deletion
from django.db import migrations, models


def _existing_columns(schema_editor, table):
    """Column names currently present on ``table``."""
    with schema_editor.connection.cursor() as cursor:
        return {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table
            )
        }


def _existing_constraints(schema_editor, table):
    """Names of the indexes and constraints currently on ``table``."""
    with schema_editor.connection.cursor() as cursor:
        return set(
            schema_editor.connection.introspection.get_constraints(cursor, table)
        )


class AddFieldIfMissing(migrations.AddField):
    """``AddField`` that leaves an already-present column alone.

    The state half is inherited untouched — the field is added to the app's
    historical models either way — so the only thing conditional here is the
    ``ALTER TABLE``.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)
        if field.column in _existing_columns(schema_editor, model._meta.db_table):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)
        if field.column not in _existing_columns(schema_editor, model._meta.db_table):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class AddIndexIfMissing(migrations.AddIndex):
    """``AddIndex`` that leaves an index of the same name alone."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.index.name in _existing_constraints(schema_editor, model._meta.db_table):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if self.index.name not in _existing_constraints(
            schema_editor, model._meta.db_table
        ):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0008_project_name_max_length_200"),
        ("tasks", "0012_tasklog_completed_at"),
    ]

    operations = [
        AddFieldIfMissing(
            model_name="tasklog",
            name="reviewed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When a reviewer signed this submission off. Null means not reviewed: the pair is cleared when the reviewer undoes their review, and when the assignee reverts their own done mark.",
                null=True,
            ),
        ),
        AddFieldIfMissing(
            model_name="tasklog",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                help_text="The reviewer who signed this submission off. Kept as written even if they later stop being a reviewer on the task, so the record still names who reviewed it; nulled only if their membership row is deleted outright.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="task_logs_reviewed",
                to="projects.projectmember",
            ),
        ),
        AddIndexIfMissing(
            model_name="tasklog",
            index=models.Index(
                fields=["reviewed_by"], name="tasks_taskl_reviewe_d603b6_idx"
            ),
        ),
    ]
