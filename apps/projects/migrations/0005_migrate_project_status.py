from django.db import migrations, models


def remap_looking_for_collaborators(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Project.objects.filter(status="LOOKING_FOR_COLLABORATORS").update(status="PLANNING")


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0004_invitation_invited_user_joinrequest"),
    ]

    operations = [
        migrations.RunPython(
            remap_looking_for_collaborators,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="project",
            name="status",
            field=models.CharField(
                choices=[
                    ("PLANNING", "Planning"),
                    ("ACTIVE", "Active"),
                    ("PAUSED", "Paused"),
                    ("COMPLETED", "Completed"),
                    ("INACTIVE", "Inactive"),
                ],
                default="PLANNING",
                max_length=32,
            ),
        ),
    ]
