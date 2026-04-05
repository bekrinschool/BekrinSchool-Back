from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="examattempt",
            name="is_cheating_detected",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="examattempt",
            name="cheating_detected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
