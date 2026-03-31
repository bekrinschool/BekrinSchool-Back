"""
Student Profile, Parent/Teacher profiles, and Parent-Child relationship.
ERD: student_profile (deleted_at), parent_profile, teacher_profile, parent_student (parent_id, student_id → User).
"""
from django.db import models
from accounts.models import User


class StudentProfile(models.Model):
    """
    Student Profile — OneToOne with User (role=student).
    Soft delete: deleted_at set instead of row delete.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='student_profile',
        limit_choices_to={'role': 'student'},
    )
    grade = models.CharField(max_length=50, blank=True, null=True, help_text="Class/Grade e.g. 10, 5A")
    balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        # No MinValueValidator: balance may go negative (lesson debits)
    )
    notes = models.TextField(blank=True, null=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'student_profiles'
        verbose_name = 'Student Profile'
        verbose_name_plural = 'Student Profiles'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.full_name} ({self.user.email})"

    def save(self, *args, **kwargs):
        self.is_deleted = self.deleted_at is not None
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return self.user.full_name

    @property
    def email(self):
        return self.user.email

    @property
    def status(self):
        """Frontend expects 'active' or 'deleted'."""
        return 'deleted' if self.deleted_at else 'active'


class ParentProfile(models.Model):
    """Parent Profile — OneToOne with User (role=parent)."""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='parent_profile',
        limit_choices_to={'role': 'parent'},
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'parent_profiles'
        verbose_name = 'Parent Profile'
        verbose_name_plural = 'Parent Profiles'

    def __str__(self):
        return str(self.user)


class TeacherProfile(models.Model):
    """Teacher Profile — OneToOne with User (role=teacher)."""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='teacher_profile',
        limit_choices_to={'role': 'teacher'},
    )
    display_title = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'teacher_profiles'
        verbose_name = 'Teacher Profile'
        verbose_name_plural = 'Teacher Profiles'

    def __str__(self):
        return str(self.user)


class ParentChild(models.Model):
    """
    Parent-Child relationship (ERD: parent_student).
    Links parent User to student User; one parent can have multiple children.
    """
    parent = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='parent_children',
        limit_choices_to={'role': 'parent'},
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='parent_links',
        limit_choices_to={'role': 'student'},
    )
    relation = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'parent_student'
        verbose_name = 'Parent-Child'
        verbose_name_plural = 'Parent-Child'
        unique_together = [['parent', 'student']]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.parent.full_name} -> {self.student.full_name}"


class ImportedCredentialRecord(models.Model):
    """
    Registry of imported credentials (CSV bulk import).
    Stores encrypted initial passwords for teacher retrieval/export.
    """
    SOURCE_CSV_IMPORT = "CSV_IMPORT"
    SOURCE_CHOICES = [(SOURCE_CSV_IMPORT, "CSV Import")]

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="imported_credential_records_created",
        limit_choices_to={"role": "teacher"},
        db_column="created_by_id",
    )
    source = models.CharField(max_length=50, choices=SOURCE_CHOICES, default=SOURCE_CSV_IMPORT)

    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="imported_credential_records_as_student",
        limit_choices_to={"role": "student"},
    )
    parent = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="imported_credential_as_parent",
        limit_choices_to={"role": "parent"},
    )

    # Snapshots (redundant for export even if user is later edited)
    student_full_name = models.CharField(max_length=255)
    student_email = models.EmailField()
    parent_email = models.EmailField(blank=True, default="")
    grade = models.CharField(max_length=50, blank=True, null=True)

    # Encrypted initial passwords (student + parent)
    initial_password_encrypted = models.TextField(blank=True, null=True)
    password_is_one_time = models.BooleanField(default=True)
    password_viewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "imported_credential_records"
        verbose_name = "Imported Credential Record"
        verbose_name_plural = "Imported Credential Records"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by"]),
            models.Index(fields=["student"]),
        ]

    def __str__(self):
        return f"{self.student_full_name} ({self.student_email})"


class BalanceLedger(models.Model):
    """
    Balance ledger for audit trail. Prevents duplicate charges via unique constraint.
    Stores all balance changes: lesson charges, topups, etc.
    """
    REASON_LESSON_CHARGE = "LESSON_CHARGE"
    REASON_TOPUP = "TOPUP"
    REASON_MANUAL = "MANUAL"
    
    REASON_CHOICES = [
        (REASON_LESSON_CHARGE, "Lesson Charge"),
        (REASON_TOPUP, "Top-up"),
        (REASON_MANUAL, "Manual Adjustment"),
    ]
    
    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="balance_ledger_entries",
        db_index=True,
    )
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="balance_ledger_entries",
        db_index=True,
    )
    date = models.DateField(db_index=True, help_text="Date of the transaction")
    amount_delta = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Negative for charges, positive for topups",
    )
    reason = models.CharField(max_length=50, choices=REASON_CHOICES, db_index=True)
    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Human-readable line for history (e.g. Dərs iştirakı - 2025-03-24)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = "balance_ledger"
        verbose_name = "Balance Ledger Entry"
        verbose_name_plural = "Balance Ledger Entries"
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student_profile", "group", "date", "reason"],
                name="unique_student_group_date_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["student_profile", "date"]),
            models.Index(fields=["group", "date"]),
        ]
    
    def __str__(self):
        return f"{self.student_profile.user.full_name} - {self.date} - {self.reason} - {self.amount_delta}"


class BalanceTransaction(models.Model):
    """
    Audit record for balance changes. lesson_debit: one per student per group per lesson_date.
    Prevents double-charging via UniqueConstraint(student_profile, group, lesson_date, type).
    DEPRECATED: Use BalanceLedger instead. Kept for backward compatibility.
    """
    TYPE_LESSON_DEBIT = "lesson_debit"
    TYPE_CHOICES = [(TYPE_LESSON_DEBIT, "Dərs haqqı çıxılışı")]

    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="balance_transactions",
        db_index=True,
    )
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="balance_transactions",
        db_index=True,
    )
    lesson_date = models.DateField(db_index=True)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Negative for debit (e.g. -12.50)",
    )
    type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_LESSON_DEBIT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "balance_transactions"
        verbose_name = "Balance Transaction"
        verbose_name_plural = "Balance Transactions"
        constraints = [
            models.UniqueConstraint(
                fields=["student_profile", "group", "lesson_date", "type"],
                name="unique_student_group_lesson_type",
            ),
        ]
        indexes = [
            models.Index(fields=["student_profile_id", "lesson_date"]),
            models.Index(fields=["group_id", "lesson_date"]),
        ]
        ordering = ["-lesson_date", "-created_at"]

    def __str__(self):
        return f"{self.student_profile} {self.lesson_date} {self.amount}"
