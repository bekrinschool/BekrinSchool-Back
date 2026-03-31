"""
Test payment balance update and notification auto-resolve.
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from students.models import StudentProfile, BalanceLedger
from groups.models import Group, GroupStudent
from payments.models import Payment
from payments.serializers import PaymentCreateSerializer
from notifications.models import Notification
from core.models import Organization

User = get_user_model()


class PaymentBalanceUpdateTests(TestCase):
    """Test that payment creation updates student balance correctly."""
    
    def setUp(self):
        self.org = Organization.objects.create(slug="test-org", name="Test Org")
        self.teacher = User.objects.create_user(
            email="teacher@test.com",
            password="test123",
            full_name="Test Teacher",
            role="teacher",
            organization=self.org,
        )
        self.student_user = User.objects.create_user(
            email="student@test.com",
            password="test123",
            full_name="Test Student",
            role="student",
            organization=self.org,
        )
        self.student_profile = StudentProfile.objects.create(
            user=self.student_user,
            grade="5A",
            balance=Decimal("0.00"),  # Start with 0 balance
        )
        self.group = Group.objects.create(
            name="Test Group",
            organization=self.org,
            created_by=self.teacher,
            monthly_fee=Decimal("100.00"),
            monthly_lessons_count=8,
        )
        GroupStudent.objects.create(
            group=self.group,
            student_profile=self.student_profile,
            active=True,
        )
    
    def test_payment_updates_balance(self):
        """Payment with status='paid' should increase student balance."""
        serializer = PaymentCreateSerializer(data={
            'studentId': self.student_profile.id,
            'groupId': self.group.id,
            'amount': Decimal('100.00'),
            'date': '2024-01-15',
            'method': 'cash',
            'status': 'paid',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        
        payment = serializer.save(created_by=self.teacher, organization=self.org)
        
        # Refresh student to get updated balance
        self.student_profile.refresh_from_db()
        
        # Balance should be 0 + 100 = 100
        self.assertEqual(self.student_profile.balance, Decimal('100.00'))
        
        # BalanceLedger entry should exist
        ledger = BalanceLedger.objects.filter(
            student_profile=self.student_profile,
            reason=BalanceLedger.REASON_TOPUP,
        ).first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.amount_delta, Decimal('100.00'))
    
    def test_payment_pending_does_not_update_balance(self):
        """Payment with status='pending' should NOT update balance."""
        serializer = PaymentCreateSerializer(data={
            'studentId': self.student_profile.id,
            'groupId': self.group.id,
            'amount': Decimal('100.00'),
            'date': '2024-01-15',
            'method': 'cash',
            'status': 'pending',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        
        old_balance = self.student_profile.balance
        payment = serializer.save(created_by=self.teacher, organization=self.org)
        
        # Refresh student
        self.student_profile.refresh_from_db()
        
        # Balance should NOT change
        self.assertEqual(self.student_profile.balance, old_balance)
        
        # No BalanceLedger entry for pending payments
        ledger = BalanceLedger.objects.filter(
            student_profile=self.student_profile,
            reason=BalanceLedger.REASON_TOPUP,
        ).first()
        self.assertIsNone(ledger)
    
    def test_payment_auto_resolves_notification(self):
        """Payment that increases balance above 0 should auto-resolve BALANCE_ZERO notification."""
        # Set balance to 0 and create notification
        self.student_profile.balance = Decimal('0.00')
        self.student_profile.save()
        
        Notification.objects.create(
            type=Notification.TYPE_BALANCE_ZERO,
            student=self.student_profile,
            group=self.group,
            message="Balance is zero",
            is_resolved=False,
        )
        
        # Create payment
        serializer = PaymentCreateSerializer(data={
            'studentId': self.student_profile.id,
            'groupId': self.group.id,
            'amount': Decimal('50.00'),
            'date': '2024-01-15',
            'method': 'cash',
            'status': 'paid',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        
        payment = serializer.save(created_by=self.teacher, organization=self.org)
        
        # Refresh student
        self.student_profile.refresh_from_db()
        
        # Balance should be 50
        self.assertEqual(self.student_profile.balance, Decimal('50.00'))
        
        # Notification should be resolved
        notification = Notification.objects.get(student=self.student_profile)
        self.assertTrue(notification.is_resolved)
        self.assertIsNotNone(notification.resolved_at)
    
    def test_multiple_payments_accumulate(self):
        """Multiple payments should accumulate balance correctly."""
        # First payment: 50
        serializer1 = PaymentCreateSerializer(data={
            'studentId': self.student_profile.id,
            'groupId': self.group.id,
            'amount': Decimal('50.00'),
            'date': '2024-01-15',
            'method': 'cash',
            'status': 'paid',
        })
        self.assertTrue(serializer1.is_valid(), serializer1.errors)
        serializer1.save(created_by=self.teacher, organization=self.org)
        
        self.student_profile.refresh_from_db()
        self.assertEqual(self.student_profile.balance, Decimal('50.00'))
        
        # Second payment: 30
        serializer2 = PaymentCreateSerializer(data={
            'studentId': self.student_profile.id,
            'groupId': self.group.id,
            'amount': Decimal('30.00'),
            'date': '2024-01-16',
            'method': 'card',
            'status': 'paid',
        })
        self.assertTrue(serializer2.is_valid(), serializer2.errors)
        serializer2.save(created_by=self.teacher, organization=self.org)
        
        self.student_profile.refresh_from_db()
        self.assertEqual(self.student_profile.balance, Decimal('80.00'))
        
        # Should have 2 ledger entries
        ledger_count = BalanceLedger.objects.filter(
            student_profile=self.student_profile,
            reason=BalanceLedger.REASON_TOPUP,
        ).count()
        self.assertEqual(ledger_count, 2)
