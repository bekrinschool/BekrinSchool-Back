"""
Lesson finalize service: when teacher clicks Save, finalize the lesson and charge students.
Uses LessonHeld and BalanceLedger for idempotent charging.
Balance deduction uses F() expression so the DB performs the update (guaranteed persistence).
"""
import logging
from decimal import Decimal
from django.db import transaction
from django.conf import settings

from groups.models import Group
from groups.services import get_active_students_for_group
from attendance.models import LessonHeld, AttendanceRecord
from notifications.services import check_and_create_balance_notifications
from students.services.wallet_transactions import charge_student_for_lesson

logger = logging.getLogger(__name__)


def _weekday_iso(lesson_date):
    """Python date.weekday(): Mon=0, Sun=6. Map to Mon=1..Sun=7."""
    return lesson_date.weekday() + 1


def _per_lesson_fee(group: Group):
    """per_lesson_fee = monthly_fee / monthly_lessons_count, quantize 0.01."""
    fee = group.monthly_fee
    count = group.monthly_lessons_count or 8
    if fee is None or fee <= 0 or count <= 0:
        return Decimal("0.00")
    return (Decimal(fee) / Decimal(count)).quantize(Decimal("0.01"))


def finalize_lesson_and_charge(group: Group, lesson_date, created_by=None):
    """
    Finalize a lesson (teacher clicked Save) and charge all active students.
    Idempotent: if LessonHeld already exists for (group, date), no charge is made.
    
    Returns:
        (lesson_held_created: bool, students_charged: int, charge_details: list)
        charge_details: [{studentId, oldBalance, newBalance, chargeAmount}]
    """
    logger.info(f"[finalize_lesson] Called: group_id={group.id}, name={group.name}, date={lesson_date}, created_by={created_by}")
    
    # Check if lesson date matches group schedule
    schedule_days = getattr(group, "schedule_days", None) or getattr(group, "days_of_week", None) or []
    logger.info(f"[finalize_lesson] schedule_days={schedule_days}, days_of_week={getattr(group, 'days_of_week', None)}")
    
    if schedule_days:
        weekday = _weekday_iso(lesson_date)
        valid_days = set(int(d) for d in schedule_days if 1 <= int(d) <= 7)
        logger.info(f"[finalize_lesson] weekday={weekday}, valid_days={valid_days}")
        if weekday not in valid_days:
            logger.warning(f"[finalize_lesson] Weekday {weekday} not in {valid_days}, skipping charge (but lesson can still be finalized)")
            # NOTE: We still allow finalizing even if schedule doesn't match (teacher override)
            # If you want strict schedule check, uncomment:
            # return False, 0
    
    per_lesson = _per_lesson_fee(group)
    logger.info(f"[finalize_lesson] monthly_fee={group.monthly_fee}, lessons_count={group.monthly_lessons_count}, per_lesson={per_lesson}")
    
    if per_lesson <= 0:
        logger.warning(f"[finalize_lesson] per_lesson={per_lesson} <= 0, cannot charge. Check group.monthly_fee and monthly_lessons_count")
        return False, 0, []
    
    with transaction.atomic():
        # Create LessonHeld record (idempotent)
        lesson_held, created = LessonHeld.objects.get_or_create(
            group=group,
            date=lesson_date,
            defaults={'created_by': created_by, 'is_finalized': True},
        )
        # If already exists but not finalized, finalize it now
        if not created and not lesson_held.is_finalized:
            lesson_held.is_finalized = True
            lesson_held.save(update_fields=['is_finalized'])
        
        logger.info(f"[finalize_lesson] LessonHeld get_or_create: created={created}, id={lesson_held.id}")
        
        if not created:
            # Lesson already finalized, no charge
            logger.info(f"[finalize_lesson] Lesson already finalized (id={lesson_held.id}), skipping charge (idempotent)")
            return False, 0, []
        
        # Get active students
        memberships = get_active_students_for_group(group)
        students = [m.student_profile for m in memberships if not m.student_profile.is_deleted]
        
        logger.info(f"[finalize_lesson] Active students: {len(students)}")
        
        if not students:
            logger.warning(f"[finalize_lesson] No active students in group, cannot charge")
            return True, 0, []
        
        # Get attendance records for this lesson date to check excused status
        attendance_records = {
            ar.student_profile_id: ar.status
            for ar in AttendanceRecord.objects.filter(
                group=group,
                lesson_date=lesson_date
            ).select_related('student_profile')
        }
        
        logger.info(f"[finalize_lesson] Charge amount per student: {per_lesson} AZN")
        
        # Charge only Present or Late by policy.
        eligible_statuses = {
            AttendanceRecord.STATUS_PRESENT,
            AttendanceRecord.STATUS_LATE,
        }

        charge_details = []
        
        for sp in students:
            attendance_status = attendance_records.get(sp.id)
            if attendance_status not in eligible_statuses:
                logger.info(
                    f"[finalize_lesson] Student {sp.id} ({sp.user.full_name}) "
                    f"status={attendance_status!r} not chargeable by policy, skipping"
                )
                continue

            result = charge_student_for_lesson(
                student=sp,
                group=group,
                lesson_date=lesson_date,
                per_lesson_fee=per_lesson,
            )
            charge_details.append({
                "studentId": str(result.student_id),
                "oldBalance": float(result.old_balance),
                "newBalance": float(result.new_balance),
                "chargeAmount": float(result.charged_amount),
            })
        
        # Keep existing <=0 alert behavior in addition to negative-cross alerts.
        try:
            charged_ids = {int(c["studentId"]) for c in charge_details}
            for sp in students:
                if sp.id not in charged_ids:
                    continue
                sp.refresh_from_db(fields=["balance"])
                check_and_create_balance_notifications(sp, group=group)
        except Exception as e:
            logger.error(f"[finalize_lesson] Error creating notifications: {e}", exc_info=True)
            # Don't fail the charge operation if notification creation fails
        
        logger.info(f"[finalize_lesson] Successfully charged {len(charge_details)} students")
        return True, len(charge_details), charge_details
