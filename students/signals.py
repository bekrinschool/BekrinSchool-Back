"""
Signals to ensure StudentProfile/ParentProfile/TeacherProfile exist when User is created or role changes.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from accounts.models import User
from .models import StudentProfile, ParentProfile, TeacherProfile


@receiver(post_save, sender=User)
def ensure_profile_exists(sender, instance, created, **kwargs):
    """
    Ensure profile exists when User is created or when role changes to student/parent/teacher.
    Handles admin-created users and role edits.
    """
    if instance.role == 'student':
        StudentProfile.objects.get_or_create(user=instance, defaults={'balance': 0})
    elif instance.role == 'parent':
        ParentProfile.objects.get_or_create(user=instance)
    elif instance.role == 'teacher':
        TeacherProfile.objects.get_or_create(user=instance)
