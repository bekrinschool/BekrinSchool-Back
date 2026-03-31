# Generated manually for presentation-order sync (teacher vs student)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tests', '0017_examattempt_result_session_soft_delete'),
    ]

    operations = [
        migrations.AddField(
            model_name='examattempt',
            name='shuffled_question_order',
            field=models.JSONField(
                blank=True,
                help_text='Presentation order snapshot: list of {questionId} (BANK) or {questionNumber} (PDF/JSON) per attempt_blueprint item',
                null=True,
            ),
        ),
    ]
