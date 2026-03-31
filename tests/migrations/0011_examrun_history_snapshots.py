from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0010_examrun_suspended_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="examrun",
            name="group_name_snapshot",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="examrun",
            name="student_name_snapshot",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="examrun",
            name="is_history_deleted",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="examrun",
            name="history_deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
