"""
Lesson charge service: when the first attendance for a group+date is saved,
open a lesson session and debit all active students once (idempotent).
Mon=1 .. Sun=7 (Azeri UI).
Balance deduction uses F() so the DB performs the update (guaranteed persistence).
"""
import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.conf import settings

from groups.models import Group
from groups.services import get_active_students_for_group
from attendance.models import GroupLessonSession
from students.models import StudentProfile, BalanceTransaction

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


def maybe_open_session_and_charge(group: Group, lesson_date):
    """
    If (group, lesson_date) has no GroupLessonSession yet and lesson_date
    is in group.schedule_days (weekday), create session and debit all
    active students. Otherwise do nothing.
    - schedule_days: group.days_of_week (1=Mon..7=Sun).
    - Idempotent: session created only once per group+date; debits once per student.
    """
    if settings.DEBUG:
        logger.debug(f"[lesson_charge] Called: group_id={group.id}, date={lesson_date}")
    
    schedule_days = getattr(group, "schedule_days", None) or getattr(group, "days_of_week", None) or []
    if settings.DEBUG:
        logger.debug(f"[lesson_charge] schedule_days={schedule_days}, days_of_week={getattr(group, 'days_of_week', None)}")
    
    if not schedule_days:
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] No schedule_days, skipping")
        return
    
    weekday = _weekday_iso(lesson_date)
    valid_days = set(int(d) for d in schedule_days if 1 <= int(d) <= 7)
    if settings.DEBUG:
        logger.debug(f"[lesson_charge] weekday={weekday}, valid_days={valid_days}")
    
    if weekday not in valid_days:
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] Weekday {weekday} not in {valid_days}, skipping")
        return

    per_lesson = _per_lesson_fee(group)
    if settings.DEBUG:
        logger.debug(f"[lesson_charge] monthly_fee={group.monthly_fee}, lessons_count={group.monthly_lessons_count}, per_lesson={per_lesson}")
    
    if per_lesson <= 0:
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] per_lesson={per_lesson} <= 0, skipping")
        return

    with transaction.atomic():
        session, created = GroupLessonSession.objects.get_or_create(
            group=group,
            lesson_date=lesson_date,
        )
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] Session get_or_create: created={created}, session_id={session.id if session else None}")
        
        if not created:
            if settings.DEBUG:
                logger.debug(f"[lesson_charge] Session already exists, skipping charge")
            return

        memberships = get_active_students_for_group(group)
        students = [m.student_profile for m in memberships if not m.student_profile.is_deleted]
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] Active students: {len(students)}")
        
        if not students:
            if settings.DEBUG:
                logger.debug(f"[lesson_charge] No active students, skipping")
            return

        debit_amount = -per_lesson
        student_ids = [sp.id for sp in students]
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] Debit amount: {debit_amount}")
            for sp in students:
                logger.debug(f"[lesson_charge] BEFORE BALANCE: student_id={sp.id} balance={sp.balance} DEDUCTION: {debit_amount}")
        
        transactions = [
            BalanceTransaction(
                student_profile=sp,
                group=group,
                lesson_date=lesson_date,
                amount=debit_amount,
                type=BalanceTransaction.TYPE_LESSON_DEBIT,
            )
            for sp in students
        ]
        BalanceTransaction.objects.bulk_create(transactions)
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] Created {len(transactions)} BalanceTransaction records")

        # Atomic DB update: balance = balance + debit_amount (guaranteed persistence)
        updated_count = StudentProfile.objects.filter(id__in=student_ids).update(
            balance=F("balance") + debit_amount
        )
        if settings.DEBUG:
            logger.debug(f"[lesson_charge] DB UPDATE F(): updated_count={updated_count}")
            for sp in students:
                sp.refresh_from_db()
                logger.debug(f"[lesson_charge] AFTER SAVE: student_id={sp.id} balance={sp.balance}")
