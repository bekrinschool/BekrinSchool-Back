"""
Coding: topics, tasks, test cases, submissions (ERD parity).
Points optional for future removal.
"""
from django.db import models
from accounts.models import User
from students.models import StudentProfile


class CodingTopic(models.Model):
    """Topic for grouping tasks (optional)."""
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coding_topics',
        db_column='organization_id',
    )
    name = models.CharField(max_length=255)
    is_archived = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_coding_topics',
        limit_choices_to={'role': 'teacher'},
        db_column='created_by_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'coding_topics'
        verbose_name = 'Coding Topic'
        verbose_name_plural = 'Coding Topics'
        ordering = ['name']

    def __str__(self):
        return self.name


class CodingTask(models.Model):
    """
    Coding task (ERD: coding_task). Replaces/enhances CodingExercise.
    topic_name for legacy; topic_id for normalized filter.
    """
    DIFFICULTY_CHOICES = [
        ('easy', 'Easy'),
        ('medium', 'Medium'),
        ('hard', 'Hard'),
    ]
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coding_tasks',
        db_column='organization_id',
    )
    topic = models.ForeignKey(
        CodingTopic,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    topic_name = models.CharField(max_length=255, blank=True, null=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    starter_code = models.TextField(blank=True, default='')
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default='easy')
    points = models.IntegerField(null=True, blank=True)
    order_index = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_coding_tasks',
        limit_choices_to={'role': 'teacher'},
        db_column='created_by_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'coding_tasks'
        verbose_name = 'Coding Task'
        verbose_name_plural = 'Coding Tasks'
        ordering = ['order_index', 'title']
        indexes = [
            models.Index(fields=['topic']),
            models.Index(fields=['is_active']),
            models.Index(fields=['order_index']),
        ]

    def __str__(self):
        return self.title


class CodingTestCase(models.Model):
    """Test case for a task (input/expected/explanation). is_sample=True for run (preview), all used for submit."""
    task = models.ForeignKey(
        CodingTask,
        on_delete=models.CASCADE,
        related_name='test_cases',
    )
    input_data = models.TextField()
    expected = models.TextField()
    explanation = models.TextField(blank=True, null=True)
    order_index = models.IntegerField(null=True, blank=True)
    is_sample = models.BooleanField(default=True, help_text='Used for Run (student preview); all cases used for Submit')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'coding_test_cases'
        verbose_name = 'Coding Test Case'
        verbose_name_plural = 'Coding Test Cases'
        ordering = ['order_index', 'id']
        indexes = [
            models.Index(fields=['task']),
        ]

    def __str__(self):
        return f"{self.task.title} - case {self.id}"


class CodingSubmission(models.Model):
    """
    Student submission (ERD: coding_submission). For ranking and history.
    run_type: RUN = student clicked Run (2 tests); SUBMIT = full submit (all tests, saved).
    """
    RUN_TYPE_CHOICES = [
        ('RUN', 'Run'),
        ('SUBMIT', 'Submit'),
    ]
    STATUS_CHOICES = [
        ('passed', 'Passed'),   # maps to ACCEPTED
        ('failed', 'Failed'),   # maps to WRONG_ANSWER
        ('error', 'Error'),
        ('timeout', 'Timeout'),
    ]
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coding_submissions',
        db_column='organization_id',
    )
    task = models.ForeignKey(
        CodingTask,
        on_delete=models.CASCADE,
        related_name='submissions',
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='coding_submissions',
        limit_choices_to={'role': 'student'},
    )
    submitted_code = models.TextField()
    language = models.CharField(max_length=20, default='python')
    run_type = models.CharField(
        max_length=10,
        choices=RUN_TYPE_CHOICES,
        default='SUBMIT',
        db_index=True,
    )
    total_count = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    score = models.IntegerField(null=True, blank=True, help_text='Optional score for this submission')
    passed_count = models.IntegerField(null=True, blank=True)
    failed_count = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    stderr = models.TextField(blank=True, null=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    runtime_ms = models.IntegerField(null=True, blank=True)
    attempt_no = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    details_json = models.JSONField(
        default=list,
        blank=True,
        help_text='Per-test results [{test_case_id, passed, output?, expected?}]. Teacher-only for hidden.',
    )

    class Meta:
        db_table = 'coding_submissions'
        verbose_name = 'Coding Submission'
        verbose_name_plural = 'Coding Submissions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['student', 'created_at']),
            models.Index(fields=['student', 'task']),
            models.Index(fields=['task']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.student.full_name} - {self.task.title} - {self.status}"


class CodingProgress(models.Model):
    """
    Student progress per task (simplified; detailed history in CodingSubmission).
    Kept for backward compat; can be derived from submissions.
    """
    STATUS_CHOICES = [
        ('not_started', 'Not Started'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
    ]
    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name='coding_progress',
    )
    exercise = models.ForeignKey(
        CodingTask,
        on_delete=models.CASCADE,
        related_name='student_progress',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='not_started')
    score = models.IntegerField(default=0, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'coding_progress'
        verbose_name = 'Coding Progress'
        verbose_name_plural = 'Coding Progress'
        unique_together = [['student_profile', 'exercise']]
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.student_profile.user.full_name} - {self.exercise.title} - {self.status}"
