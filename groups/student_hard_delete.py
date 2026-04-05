"""Shared hard-delete logic for student profiles (teacher API + archive bulk)."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from attendance.models import AttendanceRecord
from students.models import ParentChild, StudentProfile

User = get_user_model()


def hard_delete_student_profile(student_profile: StudentProfile) -> str:
    """
    Mirrors DELETE /api/teacher/students/{id}/hard.
    Returns 'anonymized' if history forced soft anonymize, 'deleted' if physical delete, 'missing_user' if no user.
    """
    user = student_profile.user
    if not user:
        return "missing_user"
    with transaction.atomic():
        from tests.models import ExamAttempt

        has_history = ExamAttempt.objects.filter(student=user).exists() or AttendanceRecord.objects.filter(
            student_profile=student_profile
        ).exists()
        parent_ids = list(ParentChild.objects.filter(student=user).values_list("parent_id", flat=True))
        ParentChild.objects.filter(student=user).delete()
        for parent_id in parent_ids:
            if not ParentChild.objects.filter(parent_id=parent_id).exists():
                try:
                    parent_user = User.objects.get(pk=parent_id, role="parent")
                    if hasattr(parent_user, "parent_profile"):
                        parent_user.parent_profile.delete()
                    parent_user.delete()
                except (User.DoesNotExist, Exception):
                    pass
        if has_history:
            user.full_name = "Deleted Student"
            user.email = f"deleted-student-{user.id}@deleted.local"
            user.is_active = False
            user.save(update_fields=["full_name", "email", "is_active"])
            student_profile.deleted_at = timezone.now()
            student_profile.is_deleted = True
            student_profile.save(update_fields=["deleted_at", "is_deleted", "updated_at"])
            return "anonymized"
        user.delete()
    return "deleted"
