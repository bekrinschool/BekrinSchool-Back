"""
Seed test (Quiz/Exam) data: 1 quiz, 1 exam, 2 groups, 3 students.
Student1: 100% correct (quiz)
Student2: half correct (exam)
Student3: manual pending (exam with situation)
Usage: python manage.py seed_tests (run after seed_dev)
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal


class Command(BaseCommand):
    help = 'Seed test data: 1 quiz, 1 exam, attempts'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Seeding test data...'))
        from django.contrib.auth import get_user_model
        from tests.models import (
            QuestionTopic, Question, QuestionOption,
            Exam, ExamQuestion, ExamAssignment, ExamStudentAssignment,
            ExamAttempt, ExamAnswer,
        )
        from groups.models import Group, GroupStudent
        from students.models import StudentProfile

        User = get_user_model()
        teacher = User.objects.filter(role='teacher').first()
        if not teacher:
            self.stdout.write(self.style.WARNING('Run seed_dev first. No teacher found.'))
            return
        org = getattr(teacher, 'organization_id', None) or getattr(teacher, 'organization', None)
        groups = list(Group.objects.all()[:2])
        students = list(StudentProfile.objects.filter(deleted_at__isnull=True).select_related('user')[:3])
        if len(groups) < 2 or len(students) < 3:
            self.stdout.write(self.style.WARNING('Need 2 groups and 3 students. Run seed_dev first.'))
            return

        topic, _ = QuestionTopic.objects.get_or_create(
            name='Riyaziyyat',
            defaults={'order': 0, 'is_active': True, 'is_archived': False},
        )

        def make_mc(text, correct_letter, topic=topic):
            q = Question.objects.create(
                topic=topic, text=text, type='MULTIPLE_CHOICE',
                correct_answer={'option_id': None},
                answer_rule_type='EXACT_MATCH',
                created_by=teacher, is_active=True, is_archived=False,
            )
            letters = ['A', 'B', 'C', 'D']
            for i, letter in enumerate(letters):
                is_correct = letter == correct_letter
                opt = QuestionOption.objects.create(question=q, text=f'Variant {letter}', is_correct=is_correct, order=i)
                if is_correct:
                    q.correct_answer = {'option_id': opt.id}
                    q.save()
            return q

        def make_open(text, correct, rule='ORDERED_DIGITS', topic=topic):
            return Question.objects.create(
                topic=topic, text=text, type='OPEN_ORDERED',
                correct_answer=correct, answer_rule_type=rule,
                created_by=teacher, is_active=True, is_archived=False,
            )

        def make_situation(text, topic=topic):
            return Question.objects.create(
                topic=topic, text=text, type='SITUATION',
                correct_answer=None, answer_rule_type=None,
                created_by=teacher, is_active=True, is_archived=False,
            )

        now = timezone.now()

        # Quiz: 12 MC + 3 open
        quiz, _ = Exam.objects.get_or_create(
            title='Seed Quiz Riyaziyyat',
            defaults={
                'type': 'quiz', 'status': 'draft',
                'start_time': now, 'duration_minutes': 30, 'max_score': 100,
                'created_by': teacher, 'is_archived': False,
            },
        )
        quiz_qs = []
        for i in range(12):
            q = make_mc(f'Quiz sual {i+1} (qapalı)', 'A' if i % 2 == 0 else 'B')
            quiz_qs.append(q)
        for i in range(3):
            q = make_open(f'Quiz sual {13+i} (açıq)', '1,2,3')
            quiz_qs.append(q)
        for i, q in enumerate(quiz_qs):
            ExamQuestion.objects.get_or_create(exam=quiz, question=q, defaults={'order': i})
        self.stdout.write(self.style.SUCCESS(f'Quiz: {quiz.title} ({len(quiz_qs)} sual)'))

        # Exam: 22 MC + 5 open + 3 situation
        exam, _ = Exam.objects.get_or_create(
            title='Seed İmtahan Riyaziyyat',
            defaults={
                'type': 'exam', 'status': 'draft',
                'start_time': now, 'duration_minutes': 60, 'max_score': 150,
                'created_by': teacher, 'is_archived': False,
            },
        )
        exam_qs = []
        for i in range(22):
            q = make_mc(f'İmtahan sual {i+1} (qapalı)', 'A' if i % 3 == 0 else 'B')
            exam_qs.append(q)
        for i in range(5):
            q = make_open(f'İmtahan sual {23+i} (açıq)', '5,10,15')
            exam_qs.append(q)
        for i in range(3):
            q = make_situation(f'İmtahan situasiya sualı {i+1}')
            exam_qs.append(q)
        for i, q in enumerate(exam_qs):
            ExamQuestion.objects.get_or_create(exam=exam, question=q, defaults={'order': i})
        self.stdout.write(self.style.SUCCESS(f'Exam: {exam.title} ({len(exam_qs)} sual)'))

        # Activate: assign to groups, start
        quiz.status = 'active'
        quiz.save()
        exam.status = 'active'
        exam.save()
        st = now
        et = now + timedelta(minutes=60)
        for g in groups:
            ExamAssignment.objects.update_or_create(
                exam=quiz, group=g,
                defaults={'start_time': st, 'end_time': et, 'duration_minutes': 60, 'is_active': True},
            )
            ExamAssignment.objects.update_or_create(
                exam=exam, group=g,
                defaults={'start_time': st, 'end_time': et, 'duration_minutes': 60, 'is_active': True},
            )

        # Dynamic scoring: X = max_score / total_units (quiz 15 units = 12+3; exam 33 units = 22+5+2*3)
        pts_quiz = Decimal('100') / 15
        pts_exam = Decimal('150') / 33

        # Student1: 100% correct quiz
        s1 = students[0].user
        att1, _ = ExamAttempt.objects.get_or_create(
            exam=quiz, student=s1,
            defaults={
                'started_at': now - timedelta(minutes=5),
                'finished_at': now - timedelta(minutes=2),
                'expires_at': et, 'duration_minutes': 60,
                'status': 'SUBMITTED', 'auto_score': Decimal('100'),
            },
        )
        for eq in ExamQuestion.objects.filter(exam=quiz).select_related('question').order_by('order'):
            q = eq.question
            if q.type == 'MULTIPLE_CHOICE':
                correct_opt = q.options.filter(is_correct=True).first()
                ExamAnswer.objects.get_or_create(
                    attempt=att1, question=q,
                    defaults={
                        'selected_option': correct_opt,
                        'auto_score': pts_quiz,
                        'requires_manual_check': False,
                    },
                )
            else:
                ExamAnswer.objects.get_or_create(
                    attempt=att1, question=q,
                    defaults={
                        'text_answer': '1,2,3',
                        'auto_score': pts_quiz,
                        'requires_manual_check': False,
                    },
                )
        self.stdout.write(self.style.SUCCESS(f'Student1: quiz 100%'))

        # Student2: half correct exam
        s2 = students[1].user
        half_auto = pts_exam * 16
        att2, _ = ExamAttempt.objects.get_or_create(
            exam=exam, student=s2,
            defaults={
                'started_at': now - timedelta(minutes=10),
                'finished_at': now - timedelta(minutes=5),
                'expires_at': et, 'duration_minutes': 60,
                'status': 'SUBMITTED', 'auto_score': half_auto,
            },
        )
        for i, eq in enumerate(ExamQuestion.objects.filter(exam=exam).select_related('question').order_by('order')):
            q = eq.question
            correct = i < 16
            if q.type == 'MULTIPLE_CHOICE':
                correct_opt = q.options.filter(is_correct=True).first() if correct else q.options.first()
                ExamAnswer.objects.get_or_create(
                    attempt=att2, question=q,
                    defaults={
                        'selected_option': correct_opt,
                        'auto_score': pts_exam if correct else Decimal('0'),
                        'requires_manual_check': False,
                    },
                )
            elif q.type != 'SITUATION':
                ExamAnswer.objects.get_or_create(
                    attempt=att2, question=q,
                    defaults={
                        'text_answer': '5,10,15' if correct else 'wrong',
                        'auto_score': pts_exam if correct else Decimal('0'),
                        'requires_manual_check': False,
                    },
                )
            else:
                ExamAnswer.objects.get_or_create(
                    attempt=att2, question=q,
                    defaults={'requires_manual_check': True},
                )
        self.stdout.write(self.style.SUCCESS(f'Student2: exam half correct'))

        # Student3: manual pending (exam, situation awaiting teacher)
        s3 = students[2].user
        ExamStudentAssignment.objects.update_or_create(
            exam=exam, student=s3,
            defaults={'start_time': st, 'duration_minutes': 60, 'is_active': True},
        )
        auto_part = pts_exam * 27
        att3, _ = ExamAttempt.objects.get_or_create(
            exam=exam, student=s3,
            defaults={
                'started_at': now - timedelta(minutes=8),
                'finished_at': now - timedelta(minutes=3),
                'expires_at': et, 'duration_minutes': 60,
                'status': 'SUBMITTED', 'auto_score': auto_part,
            },
        )
        for i, eq in enumerate(ExamQuestion.objects.filter(exam=exam).select_related('question').order_by('order')):
            q = eq.question
            if q.type == 'MULTIPLE_CHOICE':
                correct_opt = q.options.filter(is_correct=True).first()
                ExamAnswer.objects.get_or_create(
                    attempt=att3, question=q,
                    defaults={
                        'selected_option': correct_opt,
                        'auto_score': pts_exam,
                        'requires_manual_check': False,
                    },
                )
            elif q.type != 'SITUATION':
                ExamAnswer.objects.get_or_create(
                    attempt=att3, question=q,
                    defaults={
                        'text_answer': '5,10,15',
                        'auto_score': pts_exam,
                        'requires_manual_check': False,
                    },
                )
            else:
                ExamAnswer.objects.get_or_create(
                    attempt=att3, question=q,
                    defaults={'requires_manual_check': True},
                )
        self.stdout.write(self.style.SUCCESS(f'Student3: exam manual pending'))

        self.stdout.write(self.style.SUCCESS('Test seed completed.'))
