"""
Admin configuration for attendance app
"""
from django.contrib import admin
from .models import AttendanceRecord


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    """Attendance Record Admin"""
    list_display = ['student_profile', 'group', 'lesson_date', 'status', 'created_at']
    list_filter = ['status', 'lesson_date', 'group']
    search_fields = ['student_profile__user__email', 'student_profile__user__full_name']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-lesson_date']
