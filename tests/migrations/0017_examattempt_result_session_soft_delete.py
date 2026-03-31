from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0016_exam_soft_delete"),
    ]

    operations = [
        migrations.AddField(
            model_name="examattempt",
            name="is_result_session_deleted",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Teacher hid this attempt from student/parent Köhnə imtahanlar (does not delete Exam or ExamRun)",
            ),
        ),
        migrations.AddField(
            model_name="examattempt",
            name="result_session_deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
