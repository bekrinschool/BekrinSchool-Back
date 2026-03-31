"""
Create missing StudentProfile/ParentProfile/TeacherProfile for existing Users.
Safe to run multiple times; uses get_or_create.
Usage: python manage.py sync_profiles
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from students.models import StudentProfile, ParentProfile, TeacherProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Create missing profiles for users with role student/parent/teacher'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without making changes',
        )
        parser.add_argument(
            '--set-default-org',
            action='store_true',
            help='[DEV only] Assign first org to student/parent users with null org',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write('DRY RUN - no changes will be made')

        created_sp = 0
        created_pp = 0
        created_tp = 0

        student_user_ids = set(StudentProfile.objects.values_list('user_id', flat=True))
        for user in User.objects.filter(role='student'):
            if user.id not in student_user_ids:
                if not dry_run:
                    StudentProfile.objects.get_or_create(user=user, defaults={'balance': 0})
                created_sp += 1
                self.stdout.write(f'  StudentProfile: {user.email}')

        parent_user_ids = set(ParentProfile.objects.values_list('user_id', flat=True))
        for user in User.objects.filter(role='parent'):
            if user.id not in parent_user_ids:
                if not dry_run:
                    ParentProfile.objects.get_or_create(user=user)
                created_pp += 1
                self.stdout.write(f'  ParentProfile: {user.email}')

        teacher_user_ids = set(TeacherProfile.objects.values_list('user_id', flat=True))
        for user in User.objects.filter(role='teacher'):
            if user.id not in teacher_user_ids:
                if not dry_run:
                    TeacherProfile.objects.get_or_create(user=user)
                created_tp += 1
                self.stdout.write(f'  TeacherProfile: {user.email}')

        total = created_sp + created_pp + created_tp
        if dry_run:
            self.stdout.write(self.style.WARNING(f'Would create: {created_sp} StudentProfile, {created_pp} ParentProfile, {created_tp} TeacherProfile'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Created: {created_sp} StudentProfile, {created_pp} ParentProfile, {created_tp} TeacherProfile'))
        if total == 0 and not options.get('set_default_org'):
            self.stdout.write('All users already have profiles.')

        if options.get('set_default_org') and settings.DEBUG:
            from core.models import Organization
            first_org = Organization.objects.first()
            if not first_org:
                self.stdout.write(self.style.WARNING('No organization exists, skipping --set-default-org'))
            else:
                updated = User.objects.filter(
                    role__in=('student', 'parent'),
                    organization__isnull=True
                ).update(organization_id=first_org.id)
                self.stdout.write(self.style.SUCCESS(f'Assigned org (id={first_org.id}) to {updated} users with null org'))
        elif options.get('set_default_org') and not settings.DEBUG:
            self.stdout.write(self.style.WARNING('--set-default-org only runs when DEBUG=True'))
