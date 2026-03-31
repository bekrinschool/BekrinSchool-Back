"""
Test command: seed balance test data and verify lesson charge works.
Creates: teacher, group (schedule: Mon=1, Thu=4), 2 students+parents, payments.
Then simulates attendance and checks balance changes.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from core.models import Organization
from students.models import StudentProfile, ParentProfile, ParentChild
from groups.models import Group, GroupStudent
from payments.models import Payment
from attendance.models import AttendanceRecord
from attendance.services.lesson_charge import maybe_open_session_and_charge


class Command(BaseCommand):
    help = "Seed balance test data and verify lesson charge"

    def handle(self, *args, **options):
        self.stdout.write("=== Balance Test Seed ===")
        
        # Create org
        org, _ = Organization.objects.get_or_create(
            slug="test-balance-org",
            defaults={"name": "Test Balance Org"}
        )
        
        # Create teacher
        teacher, _ = User.objects.get_or_create(
            email="teacher@balance.test",
            defaults={
                "full_name": "Test Teacher",
                "role": "teacher",
                "organization": org,
                "is_active": True,
            }
        )
        teacher.set_password("test123")
        teacher.save()
        
        # Create group with schedule [1,4] (Mon, Thu) and monthly_fee=100, lessons=8
        group, created = Group.objects.get_or_create(
            name="Test Group 1-4",
            organization=org,
            defaults={
                "created_by": teacher,
                "days_of_week": [1, 4],
                "monthly_fee": Decimal("100.00"),
                "monthly_lessons_count": 8,
                "is_active": True,
            }
        )
        # Update if exists
        if not created:
            group.days_of_week = [1, 4]
            group.monthly_fee = Decimal("100.00")
            group.monthly_lessons_count = 8
            group.created_by = teacher
            group.is_active = True
            group.save()
        
        self.stdout.write(f"✓ Group: {group.name}, schedule={group.days_of_week}, fee={group.monthly_fee}, lessons={group.monthly_lessons_count}")
        
        # Create 2 students + parents
        students_data = [
            {"name": "Student One", "email": "s1@balance.test", "parent_email": "p1@balance.test"},
            {"name": "Student Two", "email": "s2@balance.test", "parent_email": "p2@balance.test"},
        ]
        
        created_students = []
        for sd in students_data:
            student_user, _ = User.objects.get_or_create(
                email=sd["email"],
                defaults={
                    "full_name": sd["name"],
                    "role": "student",
                    "organization": org,
                    "is_active": True,
                }
            )
            student_user.set_password("test123")
            student_user.save()
            
            student_profile, _ = StudentProfile.objects.get_or_create(
                user=student_user,
                defaults={"grade": "5A", "balance": Decimal("100.00")}
            )
            if not _:
                student_profile.balance = Decimal("100.00")
                student_profile.save()
            
            parent_user, _ = User.objects.get_or_create(
                email=sd["parent_email"],
                defaults={
                    "full_name": f"{sd['name']} Parent",
                    "role": "parent",
                    "organization": org,
                    "is_active": True,
                }
            )
            parent_user.set_password("test123")
            parent_user.save()
            
            ParentProfile.objects.get_or_create(user=parent_user)
            ParentChild.objects.get_or_create(parent=parent_user, student=student_user)
            
            # Add to group
            GroupStudent.objects.get_or_create(
                group=group,
                student_profile=student_profile,
                defaults={"active": True}
            )
            
            created_students.append(student_profile)
            self.stdout.write(f"✓ Student: {sd['name']}, balance={student_profile.balance}")
        
        # Create payment records (100 AZN real payment)
        for sp in created_students:
            Payment.objects.get_or_create(
                student_profile=sp,
                group=group,
                defaults={
                    "amount": Decimal("100.00"),
                    "date": date.today() - timedelta(days=30),
                    "method": "cash",
                    "status": "paid",
                    "created_by": teacher,
                    "organization": org,
                }
            )
        
        self.stdout.write("\n=== Testing Lesson Charge ===")
        
        # Find next Monday (day 1)
        today = date.today()
        days_until_monday = (1 - today.weekday()) % 7
        if days_until_monday == 0 and today.weekday() != 1:
            days_until_monday = 7
        next_monday = today + timedelta(days=days_until_monday)
        
        self.stdout.write(f"Test date (Monday): {next_monday}")
        self.stdout.write(f"Group schedule_days: {group.schedule_days}")
        self.stdout.write(f"Group monthly_fee: {group.monthly_fee}, lessons: {group.monthly_lessons_count}")
        
        # Check initial balances
        for sp in created_students:
            sp.refresh_from_db()
            self.stdout.write(f"Initial balance for {sp.user.full_name}: {sp.balance} (real), teacher view: {sp.balance / 4}")
        
        # Simulate attendance: mark both students present
        self.stdout.write("\n--- Marking attendance ---")
        for sp in created_students:
            AttendanceRecord.objects.update_or_create(
                student_profile=sp,
                lesson_date=next_monday,
                defaults={
                    "status": "present",
                    "group": group,
                    "organization": org,
                    "marked_by": teacher,
                }
            )
        
        # Trigger charge
        self.stdout.write("Calling maybe_open_session_and_charge...")
        maybe_open_session_and_charge(group, next_monday)
        
        # Check balances after charge
        self.stdout.write("\n--- After charge ---")
        for sp in created_students:
            sp.refresh_from_db()
            expected = Decimal("100.00") - (Decimal("100.00") / 8)
            self.stdout.write(f"Balance for {sp.user.full_name}: {sp.balance} (real), teacher view: {sp.balance / 4}")
            self.stdout.write(f"  Expected: {expected}, Actual: {sp.balance}")
            if abs(sp.balance - expected) > Decimal("0.01"):
                self.stdout.write(self.style.ERROR(f"  ❌ MISMATCH!"))
            else:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Match"))
        
        # Check session created
        from attendance.models import GroupLessonSession
        session = GroupLessonSession.objects.filter(group=group, lesson_date=next_monday).first()
        if session:
            self.stdout.write(self.style.SUCCESS(f"✓ Session created: {session}"))
        else:
            self.stdout.write(self.style.ERROR(f"❌ Session NOT created"))
        
        # Check transactions
        from students.models import BalanceTransaction
        transactions = BalanceTransaction.objects.filter(group=group, lesson_date=next_monday)
        self.stdout.write(f"BalanceTransactions created: {transactions.count()}")
        for t in transactions:
            self.stdout.write(f"  {t.student_profile.user.full_name}: {t.amount}")
        
        # Test notification endpoint
        self.stdout.write("\n=== Testing Low Balance Notification ===")
        # Set one student balance to 0
        sp_low = created_students[0]
        sp_low.balance = Decimal("0.00")
        sp_low.save()
        self.stdout.write(f"Set {sp_low.user.full_name} balance to 0")
        
        # Check low balance query directly
        from students.models import BalanceTransaction
        from django.db.models import OuterRef, Subquery
        from groups.models import GroupStudent
        
        low_balance_students = StudentProfile.objects.filter(
            is_deleted=False,
            balance__lte=Decimal('0'),
        ).filter(user__organization=org)
        
        self.stdout.write(f"Students with balance <= 0: {low_balance_students.count()}")
        for sp in low_balance_students:
            self.stdout.write(f"  {sp.user.full_name}: balance={sp.balance}, teacher_view={sp.balance / 4}")
        
        self.stdout.write(self.style.SUCCESS("\n=== Test Complete ==="))
