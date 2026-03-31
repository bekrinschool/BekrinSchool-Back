"""
Admin configuration for students app
"""
from django.contrib import admin
from .models import StudentProfile, ParentChild


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    """Student Profile Admin"""
    list_display = ['user', 'grade', 'balance', 'created_at', 'deleted_at']
    list_filter = ['grade', 'created_at']
    search_fields = ['user__email', 'user__full_name', 'user__phone']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(ParentChild)
class ParentChildAdmin(admin.ModelAdmin):
    """Parent-Child Admin"""
    list_display = ['parent', 'student', 'created_at']
    list_filter = ['created_at']
    search_fields = ['parent__email', 'parent__full_name', 'student__email', 'student__full_name']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
