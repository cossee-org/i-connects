import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0001_initial"),
        ("tasks", "0007_task_completion_mode"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskActivity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.CharField(
                    choices=[
                        ("CREATED", "Created"),
                        ("MEMBER_ADDED", "Member added"),
                        ("MEMBER_DONE", "Member marked done"),
                        ("MEMBER_UNDONE", "Member undid completion"),
                    ],
                    max_length=32,
                )),
                ("task", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="activities",
                    to="tasks.task",
                )),
                ("actor", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="task_activities_acted",
                    to="projects.projectmember",
                )),
                ("member", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="task_activities_affected",
                    to="projects.projectmember",
                )),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="taskactivity",
            index=models.Index(fields=["task"], name="tasks_tacti_task_id_idx"),
        ),
    ]
