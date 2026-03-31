from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0003_attendance_unique_per_group"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancerecord",
            name="entry_state",
            field=models.CharField(
                choices=[("DRAFT", "Draft"), ("CONFIRMED", "Confirmed")],
                db_index=True,
                default="DRAFT",
                max_length=20,
            ),
        ),
    ]
