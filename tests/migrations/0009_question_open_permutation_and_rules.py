from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0008_examrun_cheating_flags"),
    ]

    operations = [
        migrations.AlterField(
            model_name="question",
            name="type",
            field=models.CharField(
                choices=[
                    ("MULTIPLE_CHOICE", "Multiple Choice"),
                    ("OPEN_SINGLE_VALUE", "Open Single Value"),
                    ("OPEN_ORDERED", "Open Ordered"),
                    ("OPEN_UNORDERED", "Open Unordered"),
                    ("OPEN_PERMUTATION", "Open Permutation"),
                    ("SITUATION", "Situation"),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="question",
            name="answer_rule_type",
            field=models.CharField(
                choices=[
                    ("EXACT_MATCH", "Exact Match"),
                    ("ORDERED_MATCH", "Ordered Match"),
                    ("UNORDERED_MATCH", "Unordered Match"),
                    ("NUMERIC_EQUAL", "Numeric Equal"),
                    ("ORDERED_DIGITS", "Ordered Digits (sequence matters)"),
                    ("UNORDERED_DIGITS", "Unordered Digits (set, order irrelevant)"),
                    ("MATCHING", "Matching (Uyğunluq: 1-a, 2-b, 3-c)"),
                    ("STRICT_ORDER", "Strict Order"),
                    ("ANY_ORDER", "Any Order"),
                ],
                default="EXACT_MATCH",
                max_length=30,
            ),
        ),
    ]

