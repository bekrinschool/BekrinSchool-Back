"""
E2E seed: teacher, groups, students, parents, question bank (15q quiz + 30q exam sets),
PDFs, exams (draft + 2 active), coding tasks + submissions.
Usage: python manage.py seed_e2e
Idempotent: uses get_or_create / unique slugs; tags E2E data by teacher email.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.core.files.base import ContentFile
from datetime import timedelta

User = get_user_model()

# Minimal valid PDF bytes
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj
xref
0 4
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
trailer<</Size 4/Root 1 0 R>>
startxref
206
%%EOF"""


class Command(BaseCommand):
    help = 'Seed E2E data: teacher_e2e, 2 groups, 6 students, 3 parents, question bank, PDFs, exams, coding'

    def add_arguments(self, parser):
        parser.add_argument('--no-delete', action='store_true', help='Do not delete existing E2E entities (default: idempotent by get_or_create)')

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting seed_e2e...'))
        with transaction.atomic():
            self._create_org_teacher_groups()
            self._create_students_parents()
            self._create_question_bank()
            self._create_pdfs()
            self._create_exams()
            self._create_coding()
        self.stdout.write(self.style.SUCCESS('seed_e2e completed. Logins: teacher_e2e@bekrinschool.az / teacher123'))
        self.stdout.write(self.style.SUCCESS('Students: student_e2e_1@... / student123 (Group A), student_e2e_4@... (Group B)'))
        self.stdout.write(self.style.SUCCESS('Parents: parent_e2e_1@bekrinschool.az / parent123 (2 children)'))

    def _create_org_teacher_groups(self):
        from core.models import Organization
        from students.models import StudentProfile, TeacherProfile
        from groups.models import Group

        self.org, _ = Organization.objects.get_or_create(
            slug='bekrin-e2e',
            defaults={'name': 'Bekrin E2E Mərkəz'},
        )
        self.teacher, created = User.objects.get_or_create(
            email='teacher_e2e@bekrinschool.az',
            defaults={
                'full_name': 'E2E Müəllim',
                'role': 'teacher',
                'is_active': True,
                'organization': self.org,
            },
        )
        self.teacher.organization = self.org
        self.teacher.set_password('teacher123')
        self.teacher.save()
        TeacherProfile.objects.get_or_create(user=self.teacher)
        self.stdout.write(self.style.SUCCESS(f'Teacher: {self.teacher.email}'))

        self.group_a, _ = Group.objects.get_or_create(
            created_by=self.teacher,
            name='Group A',
            defaults={
                'organization': self.org,
                'display_name': 'Qrup A',
                'is_active': True,
                'sort_order': 0,
            },
        )
        self.group_b, _ = Group.objects.get_or_create(
            created_by=self.teacher,
            name='Group B',
            defaults={
                'organization': self.org,
                'display_name': 'Qrup B',
                'is_active': True,
                'sort_order': 1,
            },
        )
        self.stdout.write(self.style.SUCCESS('Groups: Group A, Group B'))

    def _create_students_parents(self):
        from students.models import StudentProfile, ParentProfile, ParentChild
        from groups.models import GroupStudent

        students_data = [
            ('student_e2e_1@bekrinschool.az', 'Şagird A1', '9A', self.group_a),
            ('student_e2e_2@bekrinschool.az', 'Şagird A2', '9A', self.group_a),
            ('student_e2e_3@bekrinschool.az', 'Şagird A3', '9A', self.group_a),
            ('student_e2e_4@bekrinschool.az', 'Şagird B1', '9B', self.group_b),
            ('student_e2e_5@bekrinschool.az', 'Şagird B2', '9B', self.group_b),
            ('student_e2e_6@bekrinschool.az', 'Şagird B3', '9B', self.group_b),
        ]
        self.students = []
        for email, name, grade, group in students_data:
            user, _ = User.objects.get_or_create(
                email=email,
                defaults={
                    'full_name': name,
                    'role': 'student',
                    'is_active': True,
                    'organization': self.org,
                },
            )
            user.organization = self.org
            user.set_password('student123')
            user.save()
            profile, _ = StudentProfile.objects.get_or_create(
                user=user,
                defaults={'grade': grade, 'balance': 0},
            )
            self.students.append(profile)
            gs, _ = GroupStudent.objects.get_or_create(
                group=group,
                student_profile=profile,
                defaults={'organization': self.org, 'active': True},
            )
            if not gs.active:
                gs.active = True
                gs.save(update_fields=['active'])

        # 3 parents: P1 -> A1,A2; P2 -> A3,B1; P3 -> B2,B3
        parent_data = [
            ('parent_e2e_1@bekrinschool.az', 'Valideyn 1', [0, 1]),
            ('parent_e2e_2@bekrinschool.az', 'Valideyn 2', [2, 3]),
            ('parent_e2e_3@bekrinschool.az', 'Valideyn 3', [4, 5]),
        ]
        for email, name, indices in parent_data:
            puser, _ = User.objects.get_or_create(
                email=email,
                defaults={
                    'full_name': name,
                    'role': 'parent',
                    'is_active': True,
                    'organization': self.org,
                },
            )
            puser.organization = self.org
            puser.set_password('parent123')
            puser.save()
            ParentProfile.objects.get_or_create(user=puser)
            for i in indices:
                ParentChild.objects.get_or_create(
                    parent=puser,
                    student=self.students[i].user,
                )
        self.stdout.write(self.style.SUCCESS('6 students, 3 parents, group memberships'))

    def _create_question_bank(self):
        from tests.models import QuestionTopic, Question, QuestionOption, Exam, ExamQuestion, ExamAssignment
        from decimal import Decimal

        t1, _ = QuestionTopic.objects.get_or_create(
            name='Riyaziyyat - Mövzu 1',
            defaults={'order': 0, 'is_active': True},
        )
        t2, _ = QuestionTopic.objects.get_or_create(
            name='Riyaziyyat - Mövzu 2',
            defaults={'order': 1, 'is_active': True},
        )
        self.topics = [t1, t2]

        def make_mc(topic, text, correct_idx, opts):
            q, created = Question.objects.get_or_create(
                topic=topic,
                text=text[:200] if len(text) > 200 else text,
                type='MULTIPLE_CHOICE',
                defaults={'created_by': self.teacher, 'is_active': True},
            )
            if created:
                for i, otext in enumerate(opts):
                    opt = QuestionOption.objects.create(
                        question=q, text=otext, order=i, is_correct=(i == correct_idx),
                    )
                q.correct_answer = QuestionOption.objects.get(question=q, is_correct=True).id
                q.save(update_fields=['correct_answer'])
            return q

        def make_open(topic, text, rule, correct_text):
            q, created = Question.objects.get_or_create(
                topic=topic,
                text=text[:200] if len(text) > 200 else text,
                type='OPEN_SINGLE_VALUE',
                defaults={
                    'created_by': self.teacher,
                    'is_active': True,
                    'answer_rule_type': rule,
                    'correct_answer': correct_text,
                },
            )
            if created and isinstance(q.correct_answer, dict):
                q.correct_answer = correct_text
                q.save(update_fields=['correct_answer'])
            return q

        def make_situation(topic, text):
            return Question.objects.get_or_create(
                topic=topic,
                text=text[:200] if len(text) > 200 else text,
                type='SITUATION',
                defaults={'created_by': self.teacher, 'is_active': True},
            )[0]

        # Quiz set: 12 MC + 3 OPEN
        self.quiz_questions = []
        for i in range(12):
            q = make_mc(
                t1 if i % 2 == 0 else t2,
                f'Quiz çox seçimli sual {i+1}',
                i % 4,
                [f'A{i}', f'B{i}', f'C{i}', f'D{i}'],
            )
            self.quiz_questions.append(q)
        for i in range(3):
            q = make_open(t1, f'Quiz açıq sual {i+1}', 'EXACT_MATCH', f'cavab{i+1}')
            self.quiz_questions.append(q)

        # Exam set: 22 MC + 5 OPEN + 3 SITUATION
        self.exam_questions = []
        for i in range(22):
            q = make_mc(
                t1 if i % 2 == 0 else t2,
                f'İmtahan çox seçimli {i+1}',
                i % 4,
                [f'Variant A{i}', f'Variant B{i}', f'Variant C{i}', f'Variant D{i}'],
            )
            self.exam_questions.append(q)
        for i in range(5):
            q = make_open(t2, f'İmtahan açıq sual {i+1}', 'EXACT_MATCH', f'open{i+1}')
            self.exam_questions.append(q)
        for i in range(3):
            q = make_situation(t2, f'Situasiya sualı {i+1}')
            self.exam_questions.append(q)

        self.stdout.write(self.style.SUCCESS('Question bank: 2 topics, 15 quiz questions, 30 exam questions'))

    def _create_pdfs(self):
        from tests.models import TeacherPDF

        for title, fname in [('Keçən il test 1', 'kecen_il_test_1.pdf'), ('Keçən il test 2', 'kecen_il_test_2.pdf')]:
            pdf, created = TeacherPDF.objects.get_or_create(
                teacher=self.teacher,
                title=title,
                defaults={
                    'original_filename': fname,
                    'file_size': len(MINIMAL_PDF),
                    'is_deleted': False,
                },
            )
            if created or not pdf.file:
                pdf.file.save(fname, ContentFile(MINIMAL_PDF), save=True)
                pdf.file_size = len(MINIMAL_PDF)
                pdf.save(update_fields=['file_size'])
        self.stdout.write(self.style.SUCCESS('PDFs: Kecen il test 1, Kecen il test 2'))

    def _create_exams(self):
        from tests.models import Exam, ExamQuestion, ExamAssignment

        now = timezone.now()

        # Draft quiz (15q) -> Group A, 10 min, draft
        draft_start = now + timedelta(days=1)
        exam_draft, _ = Exam.objects.get_or_create(
            title='E2E Draft Quiz',
            created_by=self.teacher,
            defaults={
                'type': 'quiz',
                'start_time': draft_start,
                'duration_minutes': 10,
                'max_score': 100,
                'status': 'draft',
            },
        )
        exam_draft.status = 'draft'
        exam_draft.start_time = draft_start
        exam_draft.duration_minutes = 10
        exam_draft.max_score = 100
        exam_draft.save()
        ExamAssignment.objects.get_or_create(exam=exam_draft, group=self.group_a)
        for i, q in enumerate(self.quiz_questions):
            ExamQuestion.objects.get_or_create(
                exam=exam_draft,
                question=q,
                defaults={'order': i},
            )

        # Active quiz (15q) -> Group A, 5 min, start now
        active_quiz_start = now
        exam_quiz, _ = Exam.objects.get_or_create(
            title='E2E Aktiv Quiz',
            created_by=self.teacher,
            defaults={
                'type': 'quiz',
                'start_time': active_quiz_start,
                'duration_minutes': 5,
                'max_score': 100,
                'status': 'active',
            },
        )
        exam_quiz.status = 'active'
        exam_quiz.start_time = active_quiz_start
        exam_quiz.duration_minutes = 5
        exam_quiz.max_score = 100
        exam_quiz.save()
        ExamAssignment.objects.get_or_create(exam=exam_quiz, group=self.group_a)
        for i, q in enumerate(self.quiz_questions):
            ExamQuestion.objects.get_or_create(
                exam=exam_quiz,
                question=q,
                defaults={'order': i},
            )

        # Active exam (30q) -> Group B, 7 min
        exam_exam, _ = Exam.objects.get_or_create(
            title='E2E Aktiv İmtahan',
            created_by=self.teacher,
            defaults={
                'type': 'exam',
                'start_time': now,
                'duration_minutes': 7,
                'max_score': 150,
                'status': 'active',
            },
        )
        exam_exam.status = 'active'
        exam_exam.start_time = now
        exam_exam.duration_minutes = 7
        exam_exam.max_score = 150
        exam_exam.save()
        ExamAssignment.objects.get_or_create(exam=exam_exam, group=self.group_b)
        for i, q in enumerate(self.exam_questions):
            ExamQuestion.objects.get_or_create(
                exam=exam_exam,
                question=q,
                defaults={'order': i},
            )

        self.stdout.write(self.style.SUCCESS('Exams: E2E Draft Quiz, E2E Aktiv Quiz (Group A), E2E Aktiv Imtahan (Group B)'))

    def _create_coding(self):
        from coding.models import CodingTopic, CodingTask, CodingTestCase, CodingSubmission

        topic, _ = CodingTopic.objects.get_or_create(
            name='E2E Kod',
            defaults={'organization': self.org},
        )
        if not topic.organization_id:
            topic.organization = self.org
            topic.save(update_fields=['organization'])
        tasks_data = [
            ('Loops 1', 'Dövrlər tapşırığı. Girişdən bir sətir oxuyun və çap edin.', 'print(input())'),
            ('Conditions 1', 'Şərtlər tapşırığı', 'if x > 0: return True'),
            ('Arrays 1', 'Massivlər tapşırığı', 'arr = [1,2,3]'),
        ]
        self.coding_tasks = []
        for idx, (title, desc, starter) in enumerate(tasks_data):
            task, _ = CodingTask.objects.get_or_create(
                title=title,
                created_by=self.teacher,
                defaults={
                    'topic': topic,
                    'description': desc,
                    'starter_code': starter,
                    'difficulty': 'easy',
                    'is_active': True,
                    'organization': self.org,
                },
            )
            self.coding_tasks.append(task)
            # First task: 2 sample (for Run) + 2 hidden (for Submit)
            num_cases = 4 if idx == 0 else 2
            for j in range(num_cases):
                is_sample = j < 2
                inp = f'in_{idx}_{j}'
                exp = f'out_{idx}_{j}'
                CodingTestCase.objects.get_or_create(
                    task=task,
                    input_data=inp,
                    defaults={
                        'expected': exp,
                        'order_index': j,
                        'is_sample': is_sample,
                    },
                )

        for si, profile in enumerate(self.students[:4]):
            for ti, task in enumerate(self.coding_tasks):
                status = 'passed' if (si + ti) % 2 == 0 else 'failed'
                CodingSubmission.objects.get_or_create(
                    task=task,
                    student=profile.user,
                    attempt_no=1,
                    defaults={
                        'submitted_code': f'# {profile.user.full_name}',
                        'status': status,
                        'passed_count': 2 if status == 'passed' else 0,
                        'failed_count': 0 if status == 'passed' else 1,
                        'organization': self.org,
                    },
                )
                # Second attempt for some
                if ti == 0 and si < 2:
                    CodingSubmission.objects.get_or_create(
                        task=task,
                        student=profile.user,
                        attempt_no=2,
                        defaults={
                            'submitted_code': f'# attempt 2',
                            'status': 'passed',
                            'passed_count': 2,
                            'failed_count': 0,
                            'organization': self.org,
                        },
                    )

        self.stdout.write(self.style.SUCCESS('Coding: 3 tasks, 2 test cases each, submissions for 4 students'))
