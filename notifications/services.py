"""
Notification services: create, resolve, auto-resolve on balance changes.
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from students.models import StudentProfile


def create_balance_zero_notification(student_profile, group=None, created_by=None):
    """
    Create a BALANCE_ZERO notification for a student.
    Returns the created notification or None if one already exists.
    """
    # Check if active notification already exists (is_read=False means active)
    existing = Notification.objects.filter(
        student=student_profile,
        type=Notification.TYPE_BALANCE_ZERO,
        is_read=False,
    ).first()
    
    if existing:
        return existing
    
    message = f"{student_profile.user.full_name} şagirdinin balansı 0-a düşdü"
    if group:
        message += f" ({group.name} qrupu)"
    
    notification = Notification.objects.create(
        type=Notification.TYPE_BALANCE_ZERO,
        student=student_profile,
        group=group,
        message=message,
        created_by=created_by,
    )
    return notification


def auto_resolve_balance_notifications(student_profile):
    """
    Auto-resolve all BALANCE_ZERO notifications for a student if balance > 0.
    Called after balance top-up or increase.
    Marks as read (is_read=True) to remove from active notifications.
    """
    if student_profile.balance and student_profile.balance > Decimal('0'):
        updated = Notification.objects.filter(
            student=student_profile,
            type=Notification.TYPE_BALANCE_ZERO,
            is_read=False,
        ).update(
            is_read=True,
            is_resolved=True,
            resolved_at=timezone.now(),
        )
        return updated
    return 0


def check_and_create_balance_notifications(student_profile, group=None):
    """
    Check if student balance is zero and create notification if needed.
    Called after balance decreases (lesson charge).
    """
    if student_profile.balance is None or student_profile.balance <= Decimal('0'):
        create_balance_zero_notification(student_profile, group=group)
        return True
    return False


def notify_negative_balance_crossed(student_profile, group=None, old_balance=None, new_balance=None):
    """
    Create immediate red alert when balance crosses below zero.
    Trigger condition: old_balance >= 0 and new_balance < 0.
    """
    old_b = Decimal(old_balance if old_balance is not None else (student_profile.balance or Decimal("0")))
    new_b = Decimal(new_balance if new_balance is not None else (student_profile.balance or Decimal("0")))
    if not (old_b >= Decimal("0") and new_b < Decimal("0")):
        return None

    message = (
        f"Xəbərdarlıq: {student_profile.user.full_name} balans mənfiyə düşdü. "
        f"Cari balans: {new_b:.2f} AZN."
    )
    notification, _ = Notification.objects.get_or_create(
        student=student_profile,
        type=Notification.TYPE_BALANCE_LOW,
        is_read=False,
        defaults={
            "group": group,
            "message": message,
        },
    )
    return notification


def notify_exam_suspended(run, student_user, happened_at):
    """Urgent (red) teacher alert for cheating/exit suspension."""
    sp = getattr(student_user, "student_profile", None)
    notif_message = (
        f"Cheating/Exit Detected: {getattr(student_user, 'full_name', 'Şagird')} "
        f"{happened_at.strftime('%H:%M')}-də imtahandan çıxdığı üçün dayandırıldı."
    )
    return Notification.objects.create(
        type=Notification.TYPE_EXAM_SUSPENDED,
        student=sp,
        group=run.group if run.group_id else None,
        message=notif_message,
        is_read=False,
        is_resolved=False,
        created_by=run.exam.created_by,
    )


def notify_exam_result_published(attempt, score: float, group=None):
    """
    Standard (white/black) notification for student/parent feed.
    Parents read the same student-linked feed.
    """
    sp = getattr(getattr(attempt, "student", None), "student_profile", None)
    if not sp:
        return None
    notif_type = f"{Notification.TYPE_EXAM_RESULT_PUBLISHED}_{attempt.exam_id}"
    message = f"İmtahan nəticələri elan olundu. Balınız: {score:.1f}"
    notif, _ = Notification.objects.update_or_create(
        student=sp,
        type=notif_type,
        defaults={
            "message": message,
            "group": group,
            "is_read": False,
            "is_resolved": False,
            "created_by": None,
        },
    )
    return notif
