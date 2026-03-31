"""
Group services - Business logic for group operations.
Single source of truth for group membership queries.
"""
from django.db import transaction
from django.core.exceptions import ValidationError
from .models import Group, GroupStudent
from students.models import StudentProfile


def get_active_students_for_group(group):
    """
    Canonical queryset: students in group (active membership only).
    Use GroupStudent as source of truth; active=True and left_at__isnull=True.
    """
    return GroupStudent.objects.filter(
        group=group,
        active=True,
        left_at__isnull=True,
        student_profile__is_deleted=False,
        student_profile__user__is_active=True,
    ).select_related('student_profile__user')


def get_active_groups_for_student(student_profile):
    """
    Canonical queryset: groups the student belongs to (active membership).
    """
    return GroupStudent.objects.filter(
        student_profile=student_profile, active=True, left_at__isnull=True
    ).select_related('group')


@transaction.atomic
def move_student(student_id, from_group_id, to_group_id):
    """
    Move student from one group to another
    Atomic transaction to ensure data consistency
    """
    try:
        student = StudentProfile.objects.get(id=student_id, is_deleted=False)
        from_group = Group.objects.get(id=from_group_id)
        to_group = Group.objects.get(id=to_group_id)
        
        # Remove from old group
        try:
            old_membership = GroupStudent.objects.get(
                group=from_group,
                student_profile=student,
                active=True
            )
            old_membership.active = False
            old_membership.save()
        except GroupStudent.DoesNotExist:
            pass  # Student not in from_group, continue
        
        # Add to new group (or reactivate if exists)
        new_membership, created = GroupStudent.objects.get_or_create(
            group=to_group,
            student_profile=student,
            defaults={'active': True}
        )
        
        if not created:
            new_membership.active = True
            new_membership.save()
        
        return new_membership
    
    except StudentProfile.DoesNotExist:
        raise ValidationError(f"Student with id {student_id} not found")
    except Group.DoesNotExist:
        raise ValidationError(f"Group not found")
    except Exception as e:
        raise ValidationError(f"Error moving student: {str(e)}")
