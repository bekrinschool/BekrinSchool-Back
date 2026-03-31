from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import F

from students.models import BalanceLedger, StudentProfile
from notifications.services import notify_negative_balance_crossed


@dataclass
class WalletDebitResult:
    student_id: int
    old_balance: Decimal
    new_balance: Decimal
    charged_amount: Decimal


def charge_student_for_lesson(*, student: StudentProfile, group, lesson_date, per_lesson_fee: Decimal) -> WalletDebitResult:
    """
    Create wallet ledger debit and apply balance deduction atomically.
    Overdraft is allowed by design (negative balances are valid).
    """
    debit_amount = -Decimal(per_lesson_fee)
    student.refresh_from_db(fields=["balance"])
    old_balance = student.balance or Decimal("0.00")
    new_balance = old_balance + debit_amount

    line_description = f"Dərs iştirakı - {lesson_date.isoformat()}"

    BalanceLedger.objects.create(
        student_profile=student,
        group=group,
        date=lesson_date,
        amount_delta=debit_amount,
        reason=BalanceLedger.REASON_LESSON_CHARGE,
        description=line_description,
    )
    StudentProfile.objects.filter(id=student.id).update(balance=F("balance") + debit_amount)
    student.refresh_from_db(fields=["balance"])

    # Immediate red alert only when crossing from >=0 to <0
    notify_negative_balance_crossed(
        student_profile=student,
        group=group,
        old_balance=old_balance,
        new_balance=student.balance or new_balance,
    )

    return WalletDebitResult(
        student_id=student.id,
        old_balance=old_balance,
        new_balance=student.balance or new_balance,
        charged_amount=abs(debit_amount),
    )
