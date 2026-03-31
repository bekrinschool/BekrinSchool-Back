from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0007_rename_exam_run_st_run_id_2d4163_idx_exam_run_st_run_id_b2a7db_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="examrun",
            name="is_cheating_detected",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="examrun",
            name="cheating_detected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

