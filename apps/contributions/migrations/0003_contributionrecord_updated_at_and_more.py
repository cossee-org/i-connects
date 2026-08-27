
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contributions", "0002_alter_contributionrecord_contribution_type"),
        ("projects", "0008_project_name_max_length_200"),
        ("tasks", "0013_tasklog_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="contributionrecord",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name="contributionrecord",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contributions",
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="contributionrecord",
            name="task",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contributions",
                to="tasks.task",
            ),
        ),
        migrations.AlterField(
            model_name="contributionrecord",
            name="task_log",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contributions",
                to="tasks.tasklog",
            ),
        ),
        migrations.AlterField(
            model_name="contributionrecord",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contributions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
