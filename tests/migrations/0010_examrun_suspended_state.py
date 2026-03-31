from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0009_question_open_permutation_and_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="examrun",
            name="suspended_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="examrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("scheduled", "Scheduled"),
                    ("active", "Active"),
                    ("suspended", "Suspended"),
                    ("finished", "Finished"),
                ],
                db_index=True,
                default="scheduled",
                max_length=20,
            ),
        ),
    ]

