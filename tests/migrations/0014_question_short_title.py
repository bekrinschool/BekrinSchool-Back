# Generated manually for teacher-only question labels

from django.db import migrations, models


def _backfill_short_title(apps, schema_editor):
    Question = apps.get_model('tests', 'Question')
    for q in Question.objects.all().iterator():
        t = (getattr(q, 'text', None) or '').strip().replace('\n', ' ')
        if len(t) > 120:
            t = t[:120] + '…'
        if not t:
            t = f'Sual {q.pk}'
        q.short_title = t
        q.save(update_fields=['short_title'])


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tests', '0013_question_mc_option_display_option_image_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='question',
            name='short_title',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(_backfill_short_title, _noop_reverse),
        migrations.AlterField(
            model_name='question',
            name='short_title',
            field=models.CharField(
                help_text='Teacher-only label for the question bank (not shown to students).',
                max_length=255,
            ),
        ),
    ]
