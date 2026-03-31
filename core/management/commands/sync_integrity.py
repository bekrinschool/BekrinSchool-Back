"""
Database integrity + consistency sync.
Creates missing profiles, fixes org mismatches on Payment/Attendance/GroupStudent.
Usage: python manage.py sync_integrity [--apply]
Without --apply: dry-run only (report, no changes).
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Sync DB integrity: missing profiles, org consistency on Payment/Attendance/GroupStudent'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Apply fixes (default: dry-run only)',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        if not apply:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be made. Use --apply to fix.'))

        stats = {'profiles': 0, 'payments': 0, 'attendance': 0, 'group_students': 0, 'users_org': 0}

        # 1) Create missing profiles (call sync_profiles)
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('sync_profiles', stdout=out)
        out.seek(0)
        for line in out:
            if 'Created:' in line or 'Would create:' in line:
                stats['profiles'] = 1  # At least something
                break

        # 2) Users with null org (student/parent/teacher) - assign first org if single-tenant
        from core.models import Organization
        first_org = Organization.objects.first()
        users_null_count = 0
        if first_org:
            users_null_org = User.objects.filter(
                role__in=('student', 'parent', 'teacher'),
                organization__isnull=True
            )
            users_null_count = users_null_org.count()
            if users_null_count > 0:
                self.stdout.write(f'  Users with null org (student/parent/teacher): {users_null_count}')
                if apply and settings.DEBUG:
                    updated = users_null_org.update(organization_id=first_org.id)
                    stats['users_org'] = updated
                    self.stdout.write(self.style.SUCCESS(f'    Assigned org to {updated} users'))
                elif apply and not settings.DEBUG:
                    self.stdout.write(self.style.WARNING('    Skipped (only runs with DEBUG=True)'))
                else:
                    self.stdout.write(f'    Would assign org (id={first_org.id}) to {users_null_count} users')

        # 3) Payment.organization mismatch with student_profile.user.organization
        from payments.models import Payment
        from students.models import StudentProfile

        payments_mismatch = []
        for p in Payment.objects.filter(deleted_at__isnull=True).select_related(
            'student_profile__user', 'organization'
        ):
            student_org = getattr(p.student_profile.user, 'organization_id', None)
            payment_org = p.organization_id if p.organization_id else None
            if student_org and payment_org != student_org:
                payments_mismatch.append((p.id, student_org, payment_org))
            elif student_org and not payment_org:
                payments_mismatch.append((p.id, student_org, None))

        if payments_mismatch:
            self.stdout.write(f'  Payments with org mismatch or null: {len(payments_mismatch)}')
            for pid, student_org, pay_org in payments_mismatch[:5]:
                self.stdout.write(f'    Payment id={pid}: student_org={student_org}, payment_org={pay_org}')
            if len(payments_mismatch) > 5:
                self.stdout.write(f'    ... and {len(payments_mismatch) - 5} more')
            if apply:
                updated = 0
                for p in Payment.objects.filter(id__in=[x[0] for x in payments_mismatch]).select_related(
                    'student_profile__user'
                ):
                    target_org = p.student_profile.user.organization_id
                    if target_org and p.organization_id != target_org:
                        p.organization_id = target_org
                        p.save(update_fields=['organization_id'])
                        updated += 1
                stats['payments'] = updated
                self.stdout.write(self.style.SUCCESS(f'    Fixed {updated} payments'))
            else:
                self.stdout.write(f'    Would fix {len(payments_mismatch)} payments')

        # 4) Attendance.organization mismatch
        from attendance.models import AttendanceRecord

        att_mismatch = []
        for a in AttendanceRecord.objects.select_related('student_profile__user', 'group', 'organization'):
            student_org = getattr(a.student_profile.user, 'organization_id', None)
            group_org = a.group.organization_id if a.group else None
            target_org = group_org or student_org
            att_org = a.organization_id if a.organization_id else None
            if target_org and att_org != target_org:
                att_mismatch.append((a.id, target_org, att_org))
            elif target_org and not att_org:
                att_mismatch.append((a.id, target_org, None))

        if att_mismatch:
            self.stdout.write(f'  Attendance with org mismatch or null: {len(att_mismatch)}')
            if apply:
                updated = 0
                for a in AttendanceRecord.objects.filter(id__in=[x[0] for x in att_mismatch]).select_related(
                    'student_profile__user', 'group'
                ):
                    target_org = (a.group.organization_id if a.group else None) or a.student_profile.user.organization_id
                    if target_org and a.organization_id != target_org:
                        a.organization_id = target_org
                        a.save(update_fields=['organization_id'])
                        updated += 1
                stats['attendance'] = updated
                self.stdout.write(self.style.SUCCESS(f'    Fixed {updated} attendance records'))
            else:
                self.stdout.write(f'    Would fix {len(att_mismatch)} attendance records')

        # 5) GroupStudent.organization mismatch with group.organization or student
        from groups.models import GroupStudent

        gs_mismatch = []
        for gs in GroupStudent.objects.select_related('group', 'student_profile__user'):
            group_org = gs.group.organization_id
            student_org = getattr(gs.student_profile.user, 'organization_id', None)
            target_org = group_org or student_org
            gs_org = gs.organization_id if gs.organization_id else None
            if target_org and gs_org != target_org:
                gs_mismatch.append((gs.id, target_org, gs_org))
            elif target_org and not gs_org:
                gs_mismatch.append((gs.id, target_org, None))

        if gs_mismatch:
            self.stdout.write(f'  GroupStudent with org mismatch or null: {len(gs_mismatch)}')
            if apply:
                updated = 0
                for gs in GroupStudent.objects.filter(id__in=[x[0] for x in gs_mismatch]).select_related(
                    'group', 'student_profile__user'
                ):
                    target_org = gs.group.organization_id or gs.student_profile.user.organization_id
                    if target_org and gs.organization_id != target_org:
                        gs.organization_id = target_org
                        gs.save(update_fields=['organization_id'])
                        updated += 1
                stats['group_students'] = updated
                self.stdout.write(self.style.SUCCESS(f'    Fixed {updated} GroupStudent records'))
            else:
                self.stdout.write(f'    Would fix {len(gs_mismatch)} GroupStudent records')

        # Summary
        total = sum(stats.values())
        if apply and total > 0:
            self.stdout.write(self.style.SUCCESS(f'Sync complete. Updated: {stats}'))
        elif not apply and (payments_mismatch or att_mismatch or gs_mismatch or users_null_count):
            self.stdout.write(self.style.WARNING('Run with --apply to apply fixes.'))
        else:
            self.stdout.write('No integrity issues found.')
