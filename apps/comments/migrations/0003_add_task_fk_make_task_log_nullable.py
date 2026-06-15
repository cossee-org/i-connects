import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comments", "0002_initial"),
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="comment",
            name="task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="comments",
                to="tasks.task",
            ),
        ),
        migrations.AlterField(
            model_name="comment",
            name="task_log",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="comments",
                to="tasks.tasklog",
            ),
        ),
        migrations.AlterModelOptions(
            name="comment",
            options={"ordering": ["created_at"]},
        ),
        migrations.AddIndex(
            model_name="comment",
            index=models.Index(fields=["task"], name="comments_co_task_id_idx"),
        ),
    ]
