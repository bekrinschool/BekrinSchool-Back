"""
Comprehensive tests for attendance save with charging and payment clearing notifications.
Tests prove:
1. Balance decreases exactly once per group+date (idempotent)
2. Calling SAVE twice does not double-decrease
3. After payment, student is removed from low-balance notifications
"""
from decimal import Decimal
from datetime import date
import json
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework.request import Request as DRFRequest
from rest_framework.parsers import JSONParser

from students.models import StudentProfile, BalanceLedger
from groups.models import Group, GroupStudent
from attendance.models import AttendanceRecord, LessonHeld
from payments.models import Payment
from payments.serializers import PaymentCreateSerializer
from core.models import Organization

User = get_user_model()


class AttendanceChargeComprehensiveTests(TestCase):
    """Comprehensive tests proving attendance charging works correctly."""
    
    def setUp(self):
        self.org = Organization.objects.create(slug="test-org", name="Test Org")
        self.teacher = User.objects.create_user(
            email="teacher@test.com",
            password="test123",
            full_name="Test Teacher",
            role="teacher",
            organization=self.org,
        )
        
        # Create 2 students with known balance
        self.student1_user = User.objects.create_user(
            email="student1@test.com",
            password="test123",
            full_name="Student One",
            role="student",
            organization=self.org,
        )
        self.student1_profile = StudentProfile.objects.create(
            user=self.student1_user,
            grade="5A",
            balance=Decimal("100.00"),  # Start with 100
        )
        
        self.student2_user = User.objects.create_user(
            email="student2@test.com",
            password="test123",
            full_name="Student Two",
            role="student",
            organization=self.org,
        )
        self.student2_profile = StudentProfile.objects.create(
            user=self.student2_user,
            grade="5A",
            balance=Decimal("100.00"),  # Start with 100
        )
        
        # Create group with monthly_fee=100, lessons=8
        self.group = Group.objects.create(
            name="Test Group",
            organization=self.org,
            created_by=self.teacher,
            days_of_week=[1, 4],  # Monday, Thursday
            monthly_fee=Decimal("100.00"),
            monthly_lessons_count=8,
        )
        
        # Add students to group
        GroupStudent.objects.create(
            group=self.group,
            student_profile=self.student1_profile,
            active=True,
        )
        GroupStudent.objects.create(
            group=self.group,
            student_profile=self.student2_profile,
            active=True,
        )
        
        self.client = APIClient()
        token = str(AccessToken.for_user(self.teacher))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    
    def test_attendance_save_decreases_balance_exactly_once(self):
        """Save attendance with finalize=true should charge students exactly once."""
        # Use Monday (day 1) which matches group schedule [1,4]
        target_date = date(2024, 1, 15)  # Monday
        
        response = self.client.post(
            "/api/teacher/attendance/save",
            {
                "date": target_date.isoformat(),
                "groupId": str(self.group.id),
                "records": [
                    {"studentId": str(self.student1_profile.id), "status": "present"},
                    {"studentId": str(self.student2_profile.id), "status": "present"},
                ],
                "finalize": True,
            },
            format="json",
        )
        
        # Check response
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("charged"), "Lesson should be finalized and charged")
        self.assertEqual(data.get("charged_count"), 2)
        self.assertTrue(data.get("delivered_marked"))
        
        # Check proof fields
        self.assertIn("charged_students", data)
        charged_students = data["charged_students"]
        self.assertEqual(len(charged_students), 2)
        
        # Verify charge details
        for detail in charged_students:
            self.assertIn("studentId", detail)
            self.assertIn("oldBalance", detail)
            self.assertIn("newBalance", detail)
            self.assertIn("chargeAmount", detail)
            self.assertEqual(detail["oldBalance"], 100.0)
            self.assertEqual(detail["newBalance"], 87.5)  # 100 - 12.5
            self.assertEqual(detail["chargeAmount"], 12.5)  # 100/8
        
        # Refresh students
        self.student1_profile.refresh_from_db()
        self.student2_profile.refresh_from_db()
        
        # Expected: 100 - (100/8) = 87.5
        expected_balance = Decimal("87.50")
        self.assertEqual(self.student1_profile.balance, expected_balance)
        self.assertEqual(self.student2_profile.balance, expected_balance)
        
        # Check LessonHeld created
        lesson_held = LessonHeld.objects.filter(group=self.group, date=target_date).first()
        self.assertIsNotNone(lesson_held)
        self.assertEqual(lesson_held.created_by, self.teacher)
        
        # Check BalanceLedger entries created
        ledger1 = BalanceLedger.objects.filter(
            student_profile=self.student1_profile,
            group=self.group,
            date=target_date,
            reason=BalanceLedger.REASON_LESSON_CHARGE,
        ).first()
        self.assertIsNotNone(ledger1)
        self.assertEqual(ledger1.amount_delta, Decimal("-12.50"))
        
        ledger2 = BalanceLedger.objects.filter(
            student_profile=self.student2_profile,
            group=self.group,
            date=target_date,
            reason=BalanceLedger.REASON_LESSON_CHARGE,
        ).first()
        self.assertIsNotNone(ledger2)
        self.assertEqual(ledger2.amount_delta, Decimal("-12.50"))
    
    def test_attendance_save_same_date_twice_no_double_charge(self):
        """Saving attendance for same date twice should not charge again (idempotent)."""
        target_date = date(2024, 1, 15)  # Monday
        
        # First save
        response1 = self.client.post(
            "/api/teacher/attendance/save",
            {
                "date": target_date.isoformat(),
                "groupId": str(self.group.id),
                "records": [
                    {"studentId": str(self.student1_profile.id), "status": "present"},
                ],
                "finalize": True,
            },
            format="json",
        )
        
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1.get("charged"))
        
        # Get balance after first save
        self.student1_profile.refresh_from_db()
        balance_after_first = self.student1_profile.balance
        
        # Second save (same date)
        response2 = self.client.post(
            "/api/teacher/attendance/save",
            {
                "date": target_date.isoformat(),
                "groupId": str(self.group.id),
                "records": [
                    {"studentId": str(self.student1_profile.id), "status": "absent"},  # Changed status
                ],
                "finalize": True,
            },
            format="json",
        )
        
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertFalse(data2.get("charged"), "Should NOT charge again (idempotent)")
        self.assertEqual(data2.get("charged_count"), 0)
        self.assertEqual(data2.get("charged_students"), [])
        
        # Balance should NOT change
        self.student1_profile.refresh_from_db()
        self.assertEqual(self.student1_profile.balance, balance_after_first)
        
        # Should still have only 1 LessonHeld
        lesson_held_count = LessonHeld.objects.filter(group=self.group, date=target_date).count()
        self.assertEqual(lesson_held_count, 1)
        
        # Should still have only 1 BalanceLedger entry per student
        ledger_count = BalanceLedger.objects.filter(
            student_profile=self.student1_profile,
            group=self.group,
            date=target_date,
            reason=BalanceLedger.REASON_LESSON_CHARGE,
        ).count()
        self.assertEqual(ledger_count, 1)
    
    def test_payment_clears_low_balance_notification(self):
        """After payment raises balance above 0, student disappears from notifications."""
        # Set student balance to 0
        self.student1_profile.balance = Decimal("0.00")
        self.student1_profile.save()
        
        # Verify student appears in low-balance notifications
        response = self.client.get("/api/teacher/notifications/low-balance")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        student_ids = [item["studentId"] for item in data["items"]]
        self.assertIn(str(self.student1_profile.id), student_ids, "Student with balance 0 should appear in notifications")
        
        # Create payment to raise balance above 0
        serializer = PaymentCreateSerializer(data={
            'studentId': self.student1_profile.id,
            'groupId': self.group.id,
            'amount': Decimal('50.00'),
            'date': '2024-01-16',
            'method': 'cash',
            'status': 'paid',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        payment = serializer.save(created_by=self.teacher, organization=self.org)
        
        # Verify balance updated
        self.student1_profile.refresh_from_db()
        self.assertEqual(self.student1_profile.balance, Decimal('50.00'))
        
        # Verify student NO LONGER appears in notifications
        response2 = self.client.get("/api/teacher/notifications/low-balance")
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        student_ids2 = [item["studentId"] for item in data2["items"]]
        self.assertNotIn(str(self.student1_profile.id), student_ids2, "Student with balance > 0 should NOT appear in notifications")
    
    def test_attendance_save_without_monthly_fee_no_charge(self):
        """If group has no monthly_fee, no charge should occur."""
        # Create group without monthly_fee
        group_no_fee = Group.objects.create(
            name="No Fee Group",
            organization=self.org,
            created_by=self.teacher,
            days_of_week=[1, 4],
            monthly_fee=None,  # No fee
            monthly_lessons_count=8,
        )
        GroupStudent.objects.create(
            group=group_no_fee,
            student_profile=self.student1_profile,
            active=True,
        )
        
        target_date = date(2024, 1, 15)
        
        response = self.client.post(
            "/api/teacher/attendance/save",
            {
                "date": target_date.isoformat(),
                "groupId": str(group_no_fee.id),
                "records": [
                    {"studentId": str(self.student1_profile.id), "status": "present"},
                ],
                "finalize": True,
            },
            format="json",
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data.get("charged"), "Should not charge if monthly_fee is None")
        self.assertEqual(data.get("charged_count"), 0)
        
        # Balance should NOT change
        self.student1_profile.refresh_from_db()
        self.assertEqual(self.student1_profile.balance, Decimal("100.00"))

    def test_finalize_charges_present_and_late_only(self):
        """Policy: only present/late are charged; absent/excused are skipped."""
        target_date = date(2024, 1, 22)  # Monday

        response = self.client.post(
            "/api/teacher/attendance/save",
            {
                "date": target_date.isoformat(),
                "groupId": str(self.group.id),
                "records": [
                    {"studentId": str(self.student1_profile.id), "status": "present"},
                    {"studentId": str(self.student2_profile.id), "status": "late"},
                ],
                "finalize": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("charged"))
        self.assertEqual(data.get("charged_count"), 2)

        self.student1_profile.refresh_from_db()
        self.student2_profile.refresh_from_db()
        expected_balance = Decimal("87.50")
        self.assertEqual(self.student1_profile.balance, expected_balance)
        self.assertEqual(self.student2_profile.balance, expected_balance)

    def test_finalize_does_not_charge_absent_or_excused(self):
        """Policy guard: absent/excused should not debit student balance."""
        target_date = date(2024, 1, 29)  # Monday

        response = self.client.post(
            "/api/teacher/attendance/save",
            {
                "date": target_date.isoformat(),
                "groupId": str(self.group.id),
                "records": [
                    {"studentId": str(self.student1_profile.id), "status": "absent"},
                    {"studentId": str(self.student2_profile.id), "status": "excused"},
                ],
                "finalize": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("charged"))
        self.assertEqual(data.get("charged_count"), 0)

        self.student1_profile.refresh_from_db()
        self.student2_profile.refresh_from_db()
        self.assertEqual(self.student1_profile.balance, Decimal("100.00"))
        self.assertEqual(self.student2_profile.balance, Decimal("100.00"))
