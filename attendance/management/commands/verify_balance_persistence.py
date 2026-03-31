"""
Temporary verification script: pick one student, set balance, simulate attendance finalize, check DB.
Run: python manage.py verify_balance_persistence [student_id]
If student_id omitted, uses first non-deleted StudentProfile with a group.
"""
from decimal import Decimal
from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction

from students.models import StudentProfile
from groups.models import GroupStudent
from attendance.services.lesson_finalize import finalize_lesson_and_charge


class Command(BaseCommand):
    help = "Verify balance persistence: set balance, finalize one lesson, print DB value."

    def add_arguments(self, parser):
        parser.add_argument("student_id", nargs="?", type=int, help="StudentProfile id (optional)")
        parser.add_argument("--balance", type=float, default=100.0, help="Set balance to this before test")
        parser.add_argument("--dry-run", action="store_true", help="Only print what would be done")

    def handle(self, *args, **options):
        student_id = options.get("student_id")
        set_balance = Decimal(str(options["balance"]))
        dry_run = options["dry_run"]

        if student_id:
            try:
                sp = StudentProfile.objects.get(id=student_id, is_deleted=False)
            except StudentProfile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"StudentProfile id={student_id} not found or deleted"))
                return
            gs = GroupStudent.objects.filter(
                student_profile=sp, active=True, left_at__isnull=True
            ).select_related("group").filter(
                group__monthly_fee__gt=0, group__monthly_lessons_count__gt=0
            ).first()
            if not gs:
                self.stdout.write(self.style.ERROR(f"Student {sp.id} not in any active group with monthly_fee"))
                return
            group = gs.group
        else:
            # Pick first student in a group with monthly_fee
            gs = (
                GroupStudent.objects.filter(active=True, left_at__isnull=True)
                .select_related("student_profile", "group")
                .filter(group__monthly_fee__gt=0, group__monthly_lessons_count__gt=0)
                .first()
            )
            if not gs:
                self.stdout.write(self.style.ERROR("No student in a group with monthly_fee and lessons_count"))
                return
            sp = gs.student_profile
            group = gs.group
            self.stdout.write(f"Using student_id={sp.id}, group_id={group.id}, group={group.name}")

        # Use a Monday for schedule_days [1,4]
        lesson_date = date(2024, 1, 15)

        self.stdout.write(f"Student id={sp.id}, balance before={sp.balance} (type={type(sp.balance).__name__})")
        self.stdout.write(f"Setting balance to {set_balance}, then finalizing lesson {lesson_date} for group {group.id}")

        if dry_run:
            self.stdout.write("DRY RUN: skipping actual update and finalize")
            return

        with transaction.atomic():
            sp.balance = set_balance
            sp.save(update_fields=["balance"])
            self.stdout.write(f"After manual save: sp.balance={sp.balance}")
            sp.refresh_from_db()
            self.stdout.write(f"After refresh: sp.balance={sp.balance}")

            db_before = StudentProfile.objects.get(id=sp.id).balance
            self.stdout.write(f"DB read (Student.objects.get): balance={db_before}")

            lesson_finalized, students_charged, charge_details = finalize_lesson_and_charge(
                group, lesson_date, created_by=None
            )
            self.stdout.write(f"finalize_lesson_and_charge: lesson_finalized={lesson_finalized}, students_charged={students_charged}")

        # Outside transaction: read DB again
        db_after = StudentProfile.objects.get(id=sp.id).balance
        sp.refresh_from_db()
        self.stdout.write(f"AFTER SAVE: Student.objects.get(id={sp.id}).balance = {db_after}")
        self.stdout.write(f"AFTER SAVE: sp.refresh_from_db() -> sp.balance = {sp.balance}")

        if db_after == db_before and lesson_finalized and students_charged:
            self.stdout.write(self.style.ERROR("PERSISTENCE BUG: balance unchanged in DB after charge"))
        elif lesson_finalized and students_charged:
            self.stdout.write(self.style.SUCCESS("OK: balance changed in DB"))
        else:
            self.stdout.write("Lesson was not charged (already finalized or no students charged).")
