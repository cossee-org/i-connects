from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0021_tasklog_evidence"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("url", models.URLField(blank=True, default="", help_text="A link to the work (a document, a folder, a pull request).", max_length=500)),
                ("note", models.TextField(blank=True, default="", help_text="The assignee's note on this item.", max_length=1000)),
                ("position", models.PositiveSmallIntegerField(help_text="Where the assignee put this item in their list, from 0.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("task_log", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="evidence_items", to="tasks.tasklog")),
            ],
            options={
                "ordering": ["position", "id"],
                "indexes": [models.Index(fields=["task_log"], name="tasks_taske_task_lo_44624f_idx")],
            },
        ),
    ]
