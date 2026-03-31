"""
Attendance model: one record per student per day.
Unique constraint: (student_profile, group, lesson_date).
"""
from django.db import models
from students.models import StudentProfile


class AttendanceRecord(models.Model):
    """
    Daily attendance record. One record per student per day globally.
    Unique: (student_profile, lesson_date).
    """
    STATUS_PRESENT = "present"
    STATUS_ABSENT = "absent"
    STATUS_LATE = "late"
    STATUS_EXCUSED = "excused"

    STATUS_CHOICES = [
        (STATUS_PRESENT, "Present"),
        (STATUS_ABSENT, "Absent"),
        (STATUS_LATE, "Late"),
        (STATUS_EXCUSED, "Excused"),
    ]
    ENTRY_STATE_DRAFT = "DRAFT"
    ENTRY_STATE_CONFIRMED = "CONFIRMED"
    ENTRY_STATE_CHOICES = [
        (ENTRY_STATE_DRAFT, "Draft"),
        (ENTRY_STATE_CONFIRMED, "Confirmed"),
    ]

    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    lesson_date = models.DateField(db_column="lesson_date")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PRESENT,
    )
    entry_state = models.CharField(
        max_length=20,
        choices=ENTRY_STATE_CHOICES,
        default=ENTRY_STATE_DRAFT,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Optional: group for audit (which group context when marked)
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
    )
    group_name_snapshot = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    organization = models.ForeignKey(
        "core.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
        db_column="organization_id",
    )
    marked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marked_attendance",
        db_column="marked_by_id",
    )
    marked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "attendance_records"
        verbose_name = "Attendance Record"
        verbose_name_plural = "Attendance Records"
        constraints = [
            models.UniqueConstraint(
                fields=["student_profile", "group", "lesson_date"],
                name="unique_student_group_lesson_date",
            ),
        ]
        ordering = ["-lesson_date", "student_profile"]
        indexes = [
            models.Index(fields=["student_profile", "lesson_date"]),
            models.Index(fields=["group", "lesson_date"], name="attendance__group_lesson_idx"),
            models.Index(fields=["lesson_date"]),
        ]

    def __str__(self):
        return f"{self.student_profile.user.full_name} - {self.lesson_date} - {self.status}"


class GroupLessonSession(models.Model):
    """
    One record per group per lesson date. Ensures idempotent lesson charging:
    when the first attendance for that group+date is saved, we create this session
    and debit all active students once.
    DEPRECATED: Use LessonHeld instead. Kept for backward compatibility.
    """
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="lesson_sessions",
        db_index=True,
    )
    lesson_date = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "group_lesson_sessions"
        verbose_name = "Group Lesson Session"
        verbose_name_plural = "Group Lesson Sessions"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "lesson_date"],
                name="unique_group_lesson_date",
            ),
        ]
        indexes = [
            models.Index(fields=["group_id", "lesson_date"]),
        ]

    def __str__(self):
        return f"{self.group.name} - {self.lesson_date}"


class LessonHeld(models.Model):
    """
    Records when a lesson was finalized (teacher clicked Save).
    One record per group per date. Ensures idempotent lesson charging.
    """
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="lessons_held",
        db_index=True,
    )
    date = models.DateField(db_index=True, help_text="Date the lesson was held")
    is_finalized = models.BooleanField(default=True, db_index=True, help_text="Whether lesson is finalized (locked for editing)")
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lessons_held_created",
        limit_choices_to={"role": "teacher"},
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "lessons_held"
        verbose_name = "Lesson Held"
        verbose_name_plural = "Lessons Held"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "date"],
                name="unique_group_lesson_held_date",
            ),
        ]
        indexes = [
            models.Index(fields=["group_id", "date"]),
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f"{self.group.name} - {self.date}"
