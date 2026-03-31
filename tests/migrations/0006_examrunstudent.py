from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('tests', '0005_add_question_image'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExamRunStudent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='run_students', to='tests.examrun')),
                ('student', models.ForeignKey(limit_choices_to={'role': 'student'}, on_delete=django.db.models.deletion.CASCADE, related_name='exam_run_links', to='accounts.user')),
            ],
            options={
                'verbose_name': 'Exam Run Student',
                'verbose_name_plural': 'Exam Run Students',
                'db_table': 'exam_run_students',
                'indexes': [models.Index(fields=['run'], name='exam_run_st_run_id_2d4163_idx'), models.Index(fields=['student'], name='exam_run_st_student_93dbfc_idx')],
                'unique_together': {('run', 'student')},
            },
        ),
    ]

