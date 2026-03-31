"""
Admin configuration for tests app
"""
from django.contrib import admin
from .models import TestResult


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    """Test Result Admin"""
    list_display = ['test_name', 'student_profile', 'score', 'max_score', 'date']
    list_filter = ['date', 'group']
    search_fields = ['test_name', 'student_profile__user__email', 'student_profile__user__full_name']
    readonly_fields = ['created_at']
    ordering = ['-date']
