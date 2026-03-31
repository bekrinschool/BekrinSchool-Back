"""
Management command to seed development data (ERD-aligned).
Usage: python manage.py seed_dev
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from datetime import date, timedelta
import os

User = get_user_model()


class Command(BaseCommand):
    help = 'Seed development data: 1 org, 1 teacher, 3 students, 1 parent, 2 groups, attendance, payments, coding, test'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting seed data...'))

        DEFAULT_PASSWORD = '12345'

        with transaction.atomic():
            from core.models import Organization
            org, _ = Organization.objects.get_or_create(
                slug='bekrin-default',
                defaults={'name': 'Bekrin Mərkəz'},
            )
            self.stdout.write(self.style.SUCCESS(f'Organization: {org.name}'))

            # Default admin (create only if not exists to avoid IntegrityError)
            admin_email = 'admin@bekrinschool.az'
            admin, admin_created = User.objects.get_or_create(
                email=admin_email,
                defaults={
                    'full_name': 'Admin',
                    'role': 'teacher',
                    'is_active': True,
                    'is_staff': True,
                    'is_superuser': True,
                    'organization': org,
                },
            )
            admin.is_staff = True
            admin.is_superuser = True
            admin.set_password(DEFAULT_PASSWORD)
            admin.save()
            if admin_created:
                self.stdout.write(self.style.SUCCESS(f'Created admin: {admin_email}'))
            else:
                self.stdout.write(self.style.WARNING(f'Admin exists, password updated: {admin_email}'))

            teacher_email = os.getenv('SEED_TEACHER_EMAIL', 'teacher@bekrinschool.az')
            teacher_password = os.getenv('SEED_TEACHER_PASSWORD', DEFAULT_PASSWORD)

            teacher, created = User.objects.get_or_create(
                email=teacher_email,
                defaults={
                    'full_name': 'Test Müəllim',
                    'role': 'teacher',
                    'is_active': True,
                    'organization': org,
                }
            )
            teacher.organization = org
            teacher.set_password(teacher_password)
            teacher.save()
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created teacher: {teacher_email}'))
            else:
                self.stdout.write(self.style.WARNING(f'Teacher exists, password updated: {teacher_email}'))

            from students.models import StudentProfile
            student_password = os.getenv('SEED_STUDENT_PASSWORD', DEFAULT_PASSWORD)
            students_data = [
                {'email': 'student1@bekrinschool.az', 'name': 'Şagird 1', 'grade': '9A'},
                {'email': 'student2@bekrinschool.az', 'name': 'Şagird 2', 'grade': '9B'},
                {'email': 'student3@bekrinschool.az', 'name': 'Şagird 3', 'grade': '10A'},
            ]
            created_students = []
            for data in students_data:
                user, _ = User.objects.get_or_create(
                    email=data['email'],
                    defaults={
                        'full_name': data['name'],
                        'role': 'student',
                        'is_active': True,
                        'organization': org,
                    }
                )
                user.organization = org
                user.set_password(student_password)
                user.save()
                profile, _ = StudentProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        'grade': data['grade'],
                        'balance': 100.00,
                        'deleted_at': None,
                    }
                )
                created_students.append(profile)
                self.stdout.write(self.style.SUCCESS(f'Student: {data["email"]}'))

            parent_email = 'parent@bekrinschool.az'
            parent_password = os.getenv('SEED_PARENT_PASSWORD', DEFAULT_PASSWORD)
            parent_user, created = User.objects.get_or_create(
                email=parent_email,
                defaults={
                    'full_name': 'Test Valideyn',
                    'role': 'parent',
                    'is_active': True,
                    'organization': org,
                }
            )
            parent_user.organization = org
            parent_user.set_password(parent_password)
            parent_user.save()
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created parent: {parent_email}'))
            else:
                self.stdout.write(self.style.WARNING(f'Parent exists, password updated: {parent_email}'))

            from students.models import ParentChild
            for profile in created_students[:2]:
                ParentChild.objects.get_or_create(
                    parent=parent_user,
                    student=profile.user,
                )

            from groups.models import Group, GroupStudent
            groups_data = [
                {'name': '9A Qrupu', 'display_name': 'Qrup1: 1-4 11:00'},
                {'name': '9B Qrupu', 'display_name': 'Qrup2: 2-5 14:00'},
            ]
            created_groups = []
            for i, data in enumerate(groups_data):
                group, created = Group.objects.get_or_create(
                    name=data['name'],
                    defaults={
                        'organization': org,
                        'created_by': teacher,
                        'display_name': data['display_name'],
                        'is_active': True,
                        'sort_order': i,
                        'days_of_week': [1, 2, 3, 4],
                    }
                )
                created_groups.append(group)
                self.stdout.write(self.style.SUCCESS(f'Group: {data["name"]}'))

            GroupStudent.objects.get_or_create(
                group=created_groups[0],
                student_profile=created_students[0],
                defaults={'active': True, 'organization': org}
            )
            GroupStudent.objects.get_or_create(
                group=created_groups[1],
                student_profile=created_students[1],
                defaults={'active': True, 'organization': org}
            )

            from attendance.models import AttendanceRecord
            today = date.today()
            for i in range(7):
                record_date = today - timedelta(days=i)
                AttendanceRecord.objects.get_or_create(
                    group=created_groups[0],
                    student_profile=created_students[0],
                    lesson_date=record_date,
                    defaults={
                        'status': 'present' if i % 2 == 0 else 'absent',
                        'marked_by': teacher,
                        'organization': org,
                    }
                )

            from payments.models import Payment
            Payment.objects.get_or_create(
                student_profile=created_students[0],
                group=created_groups[0],
                amount=50.00,
                payment_date=today,
                defaults={
                    'title': 'Nümunə ödəniş',
                    'method': 'cash',
                    'status': 'paid',
                    'created_by': teacher,
                    'organization': org,
                }
            )

            from tests.models import TestResult
            TestResult.objects.get_or_create(
                student_profile=created_students[0],
                group=created_groups[0],
                test_name='Test 1',
                defaults={
                    'score': 85,
                    'max_score': 100,
                    'date': today,
                }
            )

            from coding.models import CodingTopic, CodingTask, CodingTestCase, CodingSubmission, CodingProgress

            # 2 topics
            topic1, _ = CodingTopic.objects.get_or_create(
                name='Python Əsasları',
                defaults={'organization': org, 'created_by': teacher, 'is_archived': False},
            )
            topic2, _ = CodingTopic.objects.get_or_create(
                name='Rəqəmlər və Döngülər',
                defaults={'organization': org, 'created_by': teacher, 'is_archived': False},
            )
            # Ensure org and created_by set
            if not topic1.created_by_id:
                topic1.created_by = teacher
                topic1.organization = org
                topic1.save()
            if not topic2.created_by_id:
                topic2.created_by = teacher
                topic2.organization = org
                topic2.save()
            self.stdout.write(self.style.SUCCESS(f'Coding topics: {topic1.name}, {topic2.name}'))

            def add_test_cases(task, cases_data):
                for i, (inp, exp, is_sample) in enumerate(cases_data):
                    CodingTestCase.objects.get_or_create(
                        task=task,
                        order_index=i,
                        defaults={
                            'input_data': inp,
                            'expected': exp,
                            'is_sample': is_sample,
                            'explanation': None,
                        },
                    )

            # Topic 1: 2 tasks
            t1, _ = CodingTask.objects.get_or_create(
                title='Salam Python',
                topic=topic1,
                defaults={
                    'description': 'Girişi oxuyun və çap edin.',
                    'difficulty': 'easy',
                    'starter_code': 's = input()\nprint(s)',
                    'is_active': True,
                    'created_by': teacher,
                    'organization': org,
                },
            )
            add_test_cases(t1, [
                ('Salam', 'Salam', True),
                ('Python', 'Python', True),
                ('A', 'A', False),
                ('X', 'X', False),
                ('Test', 'Test', False),
            ])
            t2, _ = CodingTask.objects.get_or_create(
                title='İki ədədin cəmi',
                topic=topic1,
                defaults={
                    'description': 'İki tam ədəd oxuyun və cəmini çap edin.',
                    'difficulty': 'easy',
                    'starter_code': 'a, b = map(int, input().split())\nprint(a + b)',
                    'is_active': True,
                    'created_by': teacher,
                    'organization': org,
                },
            )
            add_test_cases(t2, [
                ('2 3', '5', True),
                ('10 20', '30', True),
                ('0 0', '0', False),
                ('-1 1', '0', False),
                ('100 200', '300', False),
            ])
            # Topic 2: 2 tasks
            t3, _ = CodingTask.objects.get_or_create(
                title='Kvadrat',
                topic=topic2,
                defaults={
                    'description': 'N ədədini oxuyun və n² çap edin.',
                    'difficulty': 'easy',
                    'starter_code': 'n = int(input())\nprint(n * n)',
                    'is_active': True,
                    'created_by': teacher,
                    'organization': org,
                },
            )
            add_test_cases(t3, [
                ('5', '25', True),
                ('3', '9', True),
                ('0', '0', False),
                ('10', '100', False),
                ('7', '49', False),
            ])
            t4, _ = CodingTask.objects.get_or_create(
                title='Faktorial',
                topic=topic2,
                defaults={
                    'description': 'n ədədini oxuyun və n! hesablayın.',
                    'difficulty': 'medium',
                    'starter_code': 'n = int(input())\n# faktorial hesablayın',
                    'is_active': True,
                    'created_by': teacher,
                    'organization': org,
                },
            )
            add_test_cases(t4, [
                ('1', '1', True),
                ('5', '120', True),
                ('0', '1', False),
                ('3', '6', False),
                ('4', '24', False),
            ])
            self.stdout.write(self.style.SUCCESS('Coding tasks + test cases created'))

            # Student 1: 1 task accepted
            code_ok = 'a, b = map(int, input().split())\nprint(a + b)'
            CodingSubmission.objects.get_or_create(
                task=t2,
                student=created_students[0].user,
                run_type='SUBMIT',
                defaults={
                    'organization': org,
                    'submitted_code': code_ok,
                    'status': 'passed',
                    'passed_count': 5,
                    'failed_count': 0,
                    'total_count': 5,
                    'attempt_no': 1,
                },
            )
            CodingProgress.objects.get_or_create(
                student_profile=created_students[0],
                exercise=t2,
                defaults={'status': 'completed', 'score': 100},
            )
            # Student 2: 3 wrong attempts on t1
            existing = CodingSubmission.objects.filter(
                task=t1, student=created_students[1].user, run_type='SUBMIT',
            ).count()
            for i in range(max(0, 3 - existing)):
                CodingSubmission.objects.create(
                    task=t1,
                    student=created_students[1].user,
                    organization=org,
                    submitted_code='print("wrong")',
                    run_type='SUBMIT',
                    status='failed',
                    passed_count=0,
                    failed_count=5,
                    total_count=5,
                    attempt_no=existing + i + 1,
                )
            self.stdout.write(self.style.SUCCESS('Coding submissions (student1: 1 accepted, student2: 3 wrong)'))

        self.stdout.write(self.style.SUCCESS('Seed data completed successfully!'))
        self.stdout.write(self.style.SUCCESS('\nLogin credentials (all passwords 12345 unless overridden by env):'))
        self.stdout.write(self.style.SUCCESS(f'Admin: admin@bekrinschool.az / {DEFAULT_PASSWORD}'))
        self.stdout.write(self.style.SUCCESS(f'Teacher: {teacher_email} / {teacher_password}'))
        self.stdout.write(self.style.SUCCESS(f'Student: student1@bekrinschool.az / {student_password}'))
        self.stdout.write(self.style.SUCCESS(f'Parent: {parent_email} / {parent_password}'))
