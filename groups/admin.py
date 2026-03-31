"""
Admin configuration for groups app
"""
from django.contrib import admin
from .models import Group, GroupStudent


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    """Group Admin"""
    list_display = ['name', 'display_name', 'is_active', 'sort_order', 'created_by', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['sort_order', 'name']


@admin.register(GroupStudent)
class GroupStudentAdmin(admin.ModelAdmin):
    """Group Student Admin"""
    list_display = ['group', 'student_profile', 'active', 'joined_at']
    list_filter = ['active', 'joined_at']
    search_fields = ['group__name', 'student_profile__user__email', 'student_profile__user__full_name']
    readonly_fields = ['joined_at']
    ordering = ['-joined_at']
