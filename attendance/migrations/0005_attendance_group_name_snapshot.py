from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_attendancerecord_entry_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancerecord",
            name="group_name_snapshot",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
    ]
