from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0002_initial"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="attendancerecord",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="attendancerecord",
            constraint=models.UniqueConstraint(
                fields=("student_profile", "group", "lesson_date"),
                name="unique_student_group_lesson_date",
            ),
        ),
        migrations.AddIndex(
            model_name="attendancerecord",
            index=models.Index(fields=["group", "lesson_date"], name="attendance__group_lesson_idx"),
        ),
    ]

