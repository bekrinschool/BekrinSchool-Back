"""
Payment models
"""
from django.db import models
from django.core.validators import MinValueValidator
from accounts.models import User
from students.models import StudentProfile
from groups.models import Group
import uuid


class Payment(models.Model):
    """
    Payment record
    """
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('bank', 'Bank Transfer'),
    ]
    
    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('pending', 'Pending'),
    ]
    
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments',
        db_column='organization_id',
    )
    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name='payments',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments',
    )
    payment_date = models.DateField(db_column='payment_date')
    title = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='cash')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='paid')
    note = models.TextField(blank=True, null=True)
    receipt_no = models.CharField(max_length=50, unique=True, blank=True, null=True)
    sequence_number = models.IntegerField(null=True, blank=True, db_index=True, help_text='Sequential payment number (globally ordered by date)')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_payments',
        limit_choices_to={'role': 'teacher'},
        db_column='created_by_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    
    class Meta:
        db_table = 'payments'
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'
        ordering = ['-payment_date', '-created_at']
        indexes = [
            models.Index(fields=['student_profile', 'payment_date']),
            models.Index(fields=['group', 'payment_date']),
        ]
    
    def __str__(self):
        return f"Payment {self.receipt_no or self.id} - {self.student_profile.user.full_name} - {self.amount}"

    @property
    def date(self):
        """Backward compat: frontend may expect .date"""
        return self.payment_date
    
    def save(self, *args, **kwargs):
        """Generate receipt number if not provided"""
        if not self.receipt_no:
            self.receipt_no = f"PAY-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)
