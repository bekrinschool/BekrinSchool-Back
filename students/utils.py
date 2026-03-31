"""
Utility functions for student balance display.
"""
from decimal import Decimal


def get_teacher_display_balance(real_balance):
    """
    Convert real balance to teacher display balance.
    Teacher view: real_balance / 4
    Parent view: real_balance (no conversion)
    """
    if real_balance is None:
        return 0.0
    return round(float(real_balance) / 4, 2)


def get_real_balance_from_teacher_display(teacher_display):
    """
    Convert teacher display balance back to real balance.
    real_balance = teacher_display * 4
    """
    if teacher_display is None:
        return Decimal('0.00')
    return Decimal(str(teacher_display * 4)).quantize(Decimal('0.01'))
