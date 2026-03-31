from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0015_exam_session_control"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="exam",
            name="is_deleted",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AlterField(
            model_name="exam",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("active", "Active"),
                    ("finished", "Finished"),
                    ("archived", "Archived"),
                    ("deleted", "Deleted"),
                ],
                db_index=True,
                default="draft",
                max_length=20,
            ),
        ),
    ]
