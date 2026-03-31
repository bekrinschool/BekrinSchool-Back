"""
Notification models for teacher alerts (balance zero, etc.)
"""
from django.db import models
from accounts.models import User
from students.models import StudentProfile
from groups.models import Group


class Notification(models.Model):
    """
    Teacher notifications (balance alerts, etc.)
    """
    TYPE_BALANCE_ZERO = "BALANCE_ZERO"
    TYPE_BALANCE_LOW = "BALANCE_LOW"
    TYPE_EXAM_RESULT_PUBLISHED = "EXAM_RESULT_PUBLISHED"
    TYPE_EXAM_SUSPENDED = "EXAM_SUSPENDED"

    TYPE_CHOICES = [
        (TYPE_BALANCE_ZERO, "Balance Zero"),
        (TYPE_BALANCE_LOW, "Balance Low"),
        (TYPE_EXAM_RESULT_PUBLISHED, "Exam Result Published"),
        (TYPE_EXAM_SUSPENDED, "Exam Suspended"),
    ]
    
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, db_index=True)
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    is_resolved = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_notifications",
    )
    
    class Meta:
        db_table = "notifications"
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "type"],
                condition=models.Q(is_read=False),
                name="unique_active_notification_per_student_type"
            ),
        ]
        indexes = [
            models.Index(fields=["type", "is_read", "is_resolved"]),
            models.Index(fields=["student", "is_resolved"]),
        ]
    
    def __str__(self):
        return f"{self.type} - {self.student.user.full_name if self.student else 'General'} - {self.created_at}"
