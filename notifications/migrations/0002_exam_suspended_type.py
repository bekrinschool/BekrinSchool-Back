from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="type",
            field=models.CharField(
                choices=[
                    ("BALANCE_ZERO", "Balance Zero"),
                    ("BALANCE_LOW", "Balance Low"),
                    ("EXAM_RESULT_PUBLISHED", "Exam Result Published"),
                    ("EXAM_SUSPENDED", "Exam Suspended"),
                ],
                db_index=True,
                max_length=50,
            ),
        ),
    ]

