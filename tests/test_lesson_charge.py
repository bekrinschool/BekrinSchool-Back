"""
Unit tests for lesson charge (maybe_open_session_and_charge) and low-balance notifications.
- schedule_days=[1,4], date weekday=2 -> session not opened
- weekday=1, first attendance -> session created, all students debited once
- same day second attendance -> no double charge
- bulk 100 students works
- balance <= 0 appears in GET /api/teacher/notifications/low-balance
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from core.models import Organization
from students.models import StudentProfile, BalanceTransaction
from groups.models import Group, GroupStudent
from attendance.models import GroupLessonSession
from attendance.services.lesson_charge import maybe_open_session_and_charge


def _weekday_iso(d):
    """Mon=1 .. Sun=7."""
    return d.weekday() + 1


class LessonChargeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", slug="test-org-lesson")
        self.teacher = User.objects.create_user(
            email="t@lesson.test",
            password="pass123",
            full_name="Teacher",
            role="teacher",
            organization=self.org,
        )
        self.group = Group.objects.create(
            name="G1",
            organization=self.org,
            created_by=self.teacher,
            days_of_week=[1, 4],
            monthly_fee=Decimal("80.00"),
            monthly_lessons_count=8,
        )
        self.students = []
        for i in range(3):
            u = User.objects.create_user(
                email=f"s{i}@lesson.test",
                password="pass123",
                full_name=f"Student {i}",
                role="student",
                organization=self.org,
            )
            sp, _ = StudentProfile.objects.get_or_create(
                user=u,
                defaults={"grade": "10", "balance": Decimal("100.00")},
            )
            sp.balance = Decimal("100.00")
            sp.save()
            self.students.append(sp)
            GroupStudent.objects.get_or_create(
                group=self.group,
                student_profile=sp,
                defaults={"active": True},
            )

    def test_schedule_days_mismatch_no_session(self):
        """weekday=2 (Tue) not in [1,4] -> session not opened."""
        # 2026-02-10 is Tuesday (weekday 2)
        lesson_date = date(2026, 2, 10)
        self.assertEqual(_weekday_iso(lesson_date), 2)
        maybe_open_session_and_charge(self.group, lesson_date)
        self.assertEqual(GroupLessonSession.objects.filter(group=self.group, lesson_date=lesson_date).count(), 0)
        self.assertEqual(BalanceTransaction.objects.filter(lesson_date=lesson_date).count(), 0)

    def test_first_attendance_creates_session_and_debits_once(self):
        """weekday=1 (Mon), first call -> session created, all students debited once."""
        lesson_date = date(2026, 2, 9)
        self.assertEqual(_weekday_iso(lesson_date), 1)
        per_lesson = Decimal("80") / 8
        self.assertEqual(per_lesson, Decimal("10.00"))

        maybe_open_session_and_charge(self.group, lesson_date)

        sessions = GroupLessonSession.objects.filter(group=self.group, lesson_date=lesson_date)
        self.assertEqual(sessions.count(), 1)
        debits = BalanceTransaction.objects.filter(
            group=self.group, lesson_date=lesson_date, type=BalanceTransaction.TYPE_LESSON_DEBIT
        )
        self.assertEqual(debits.count(), 3)
        for sp in self.students:
            sp.refresh_from_db()
            self.assertEqual(sp.balance, Decimal("100.00") - per_lesson)
        for d in debits:
            self.assertEqual(d.amount, -per_lesson)

    def test_second_attendance_no_double_charge(self):
        """Same day second call -> session already exists, no new debits."""
        lesson_date = date(2026, 2, 9)
        maybe_open_session_and_charge(self.group, lesson_date)
        first_balance = {sp.id: StudentProfile.objects.get(id=sp.id).balance for sp in self.students}

        maybe_open_session_and_charge(self.group, lesson_date)

        self.assertEqual(GroupLessonSession.objects.filter(group=self.group, lesson_date=lesson_date).count(), 1)
        self.assertEqual(
            BalanceTransaction.objects.filter(
                group=self.group, lesson_date=lesson_date, type=BalanceTransaction.TYPE_LESSON_DEBIT
            ).count(),
            3,
        )
        for sp in self.students:
            sp.refresh_from_db()
            self.assertEqual(sp.balance, first_balance[sp.id])

    def test_bulk_students_charge(self):
        """Many students: all debited once."""
        extra = []
        for i in range(97):
            u = User.objects.create_user(
                email=f"bulk{i}@lesson.test",
                password="pass123",
                full_name=f"Bulk {i}",
                role="student",
                organization=self.org,
            )
            sp, _ = StudentProfile.objects.get_or_create(
                user=u,
                defaults={"grade": "9", "balance": Decimal("50.00")},
            )
            sp.balance = Decimal("50.00")
            sp.save()
            extra.append(sp)
            GroupStudent.objects.get_or_create(
                group=self.group,
                student_profile=sp,
                defaults={"active": True},
            )
        lesson_date = date(2026, 2, 12)
        self.assertEqual(_weekday_iso(lesson_date), 4)

        maybe_open_session_and_charge(self.group, lesson_date)

        total_students = 3 + 97
        self.assertEqual(
            BalanceTransaction.objects.filter(
                group=self.group, lesson_date=lesson_date, type=BalanceTransaction.TYPE_LESSON_DEBIT
            ).count(),
            total_students,
        )
        per_lesson = Decimal("10.00")
        for sp in self.students:
            sp.refresh_from_db()
            self.assertEqual(sp.balance, Decimal("100.00") - per_lesson)
        for sp in extra:
            sp.refresh_from_db()
            self.assertEqual(sp.balance, Decimal("50.00") - per_lesson)


class LowBalanceNotificationsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Org NB", slug="org-nb")
        self.teacher = User.objects.create_user(
            email="t@nb.test",
            password="pass123",
            full_name="Teacher",
            role="teacher",
            organization=self.org,
        )
        self.group = Group.objects.create(
            name="G",
            organization=self.org,
            created_by=self.teacher,
            days_of_week=[1],
        )
        self.student_low = User.objects.create_user(
            email="low@nb.test",
            password="pass123",
            full_name="Low Balance",
            role="student",
            organization=self.org,
        )
        self.sp_low, _ = StudentProfile.objects.get_or_create(
            user=self.student_low,
            defaults={"grade": "10", "balance": Decimal("-5.00")},
        )
        self.sp_low.balance = Decimal("-5.00")
        self.sp_low.save()
        GroupStudent.objects.get_or_create(
            group=self.group,
            student_profile=self.sp_low,
            defaults={"active": True},
        )
        self.student_ok = User.objects.create_user(
            email="ok@nb.test",
            password="pass123",
            full_name="OK Balance",
            role="student",
            organization=self.org,
        )
        self.sp_ok, _ = StudentProfile.objects.get_or_create(
            user=self.student_ok,
            defaults={"grade": "10", "balance": Decimal("20.00")},
        )
        self.sp_ok.balance = Decimal("20.00")
        self.sp_ok.save()
        GroupStudent.objects.get_or_create(
            group=self.group,
            student_profile=self.sp_ok,
            defaults={"active": True},
        )

    def _auth(self, user):
        token = str(AccessToken.for_user(user))
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_low_balance_appears_in_notifications(self):
        """Students with balance <= 0 appear in GET /api/teacher/notifications/low-balance."""
        # Ensure balances are correct before test
        self.sp_low.refresh_from_db()
        self.sp_ok.refresh_from_db()
        self.assertLessEqual(self.sp_low.balance, 0, f"sp_low balance should be <= 0, got {self.sp_low.balance}")
        self.assertGreater(self.sp_ok.balance, 0, f"sp_ok balance should be > 0, got {self.sp_ok.balance}")
        
        self.client.credentials(**self._auth(self.teacher))
        res = self.client.get("/api/teacher/notifications/low-balance")
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertIsInstance(data, list)
        ids = [x["studentId"] for x in data]
        self.assertIn(str(self.sp_low.id), ids, f"sp_low (balance={self.sp_low.balance}) should be in results")
        self.assertNotIn(str(self.sp_ok.id), ids, f"sp_ok (balance={self.sp_ok.balance}) should NOT be in results")
        item = next(x for x in data if x["studentId"] == str(self.sp_low.id))
        self.assertEqual(item["fullName"], "Low Balance")
        self.assertEqual(item["balance_real"], -5.0)
        self.assertEqual(item["balance_teacher_view"], -1.25)
