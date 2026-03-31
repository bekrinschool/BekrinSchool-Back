"""
Group and Group-Student relationship (ERD: group, group_membership).
Schedule: days_of_week (lesson_days), start_time, display_name (auto or manual).
"""
from django.db import models
from accounts.models import User
from students.models import StudentProfile


def derive_display_name_from_days(days: list, start_time=None) -> str:
    """
    Build compact day label from lesson days. Format: "1-4 11:00"
    Mon=1..Sun=7. "1-4" means ONLY days 1 and 4 (B.e + C.a), NOT 1 through 4.
    - [1, 4] => "1-4"
    - [1, 3, 5] => "1-3-5"
    - With start_time => "1-4 11:00"
    """
    if not days:
        return ""
    days = sorted(set(int(d) for d in days if 1 <= int(d) <= 7))
    if not days:
        return ""
    # Hyphen-separated list (each number is a day index, not a range)
    day_part = "-".join(str(d) for d in days)
    if start_time and hasattr(start_time, "strftime"):
        time_str = start_time.strftime("%H:%M")
        return f"{day_part} {time_str}".strip()
    return day_part


def parse_days_from_display_name(display_name: str):
    """
    Parse days from display_name for backward compatibility.
    "1-4 11:00" => [1, 4], "1-3-5 10:00" => [1, 3, 5].
    Returns None if parse fails.
    """
    if not display_name or not isinstance(display_name, str):
        return None
    s = display_name.strip()
    # Take part before time (HH:MM or HH.MM)
    import re
    m = re.match(r"^([\d\-,\s]+?)(?:\s+\d{1,2}[:\.]\d{2})?", s)
    day_str = (m.group(1) if m else s).strip() if s else ""
    if not day_str:
        return None
    days = []
    for part in re.split(r"[,\-\s]+", day_str):
        try:
            n = int(part)
            if 1 <= n <= 7 and n not in days:
                days.append(n)
        except (ValueError, TypeError):
            pass
    return sorted(days) if days else None


class Group(models.Model):
    """
    Group — teacher's class group with optional schedule.
    display_name can be auto-generated from days_of_week (lesson_days) unless display_name_is_manual.
    """
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='groups',
        db_column='organization_id',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_groups',
        limit_choices_to={'role': 'teacher'},
        db_column='teacher_id',
    )
    code = models.CharField(max_length=50, blank=True, null=True)
    name = models.CharField(max_length=255)
    # days_of_week (lesson_days): 1=Mon..7=Sun; stored as JSON list [1,2,3,4]
    days_of_week = models.JSONField(default=list, blank=True, help_text="e.g. [1,2,3,4]; Mon=1..Sun=7")
    start_time = models.TimeField(blank=True, null=True)
    display_name = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    display_name_is_manual = models.BooleanField(default=False, help_text="If True, do not auto-overwrite display_name")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(null=True, blank=True, default=0)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Lesson charging: real AZN
    monthly_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default=None,
        help_text="Aylıq haqq (real AZN)",
    )
    monthly_lessons_count = models.PositiveIntegerField(
        default=8,
        help_text="Ayda dərs sayı; per_lesson_fee = monthly_fee / monthly_lessons_count",
    )

    class Meta:
        db_table = 'groups'
        verbose_name = 'Group'
        verbose_name_plural = 'Groups'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.display_name or self.name

    @property
    def schedule_days(self):
        """Həftə günləri (1=Mon..7=Sun). Alias for days_of_week."""
        return getattr(self, "days_of_week", None) or []

    @property
    def student_count(self):
        return self.group_students.filter(active=True, left_at__isnull=True).count()


class GroupStudent(models.Model):
    """
    Group membership (ERD: group_membership). left_at for history.
    """
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='group_memberships',
        db_column='organization_id',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name='group_students',
    )
    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name='group_memberships',
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'group_students'
        verbose_name = 'Group Student'
        verbose_name_plural = 'Group Students'
        unique_together = [['group', 'student_profile']]
        ordering = ['-joined_at']

    def __str__(self):
        return f"{self.group.name} - {self.student_profile.user.full_name}"
