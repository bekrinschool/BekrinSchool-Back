from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0011_examrun_history_snapshots"),
    ]

    operations = [
        migrations.AlterField(
            model_name="examrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("scheduled", "Scheduled"),
                    ("active", "Active"),
                    ("suspended", "Suspended"),
                    ("finished", "Finished"),
                    ("published", "Published"),
                ],
                db_index=True,
                default="scheduled",
                max_length=20,
            ),
        ),
    ]
