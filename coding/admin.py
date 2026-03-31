"""
Admin configuration for coding app
"""
from django.contrib import admin
from .models import CodingTopic, CodingTask, CodingTestCase, CodingSubmission, CodingProgress


@admin.register(CodingTopic)
class CodingTopicAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']


@admin.register(CodingTask)
class CodingTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'difficulty', 'created_by', 'created_at']
    list_filter = ['difficulty', 'created_at']
    search_fields = ['title', 'description']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(CodingTestCase)
class CodingTestCaseAdmin(admin.ModelAdmin):
    list_display = ['task', 'order_index', 'created_at']


@admin.register(CodingSubmission)
class CodingSubmissionAdmin(admin.ModelAdmin):
    list_display = ['task', 'student', 'status', 'created_at']
    list_filter = ['status', 'created_at']


@admin.register(CodingProgress)
class CodingProgressAdmin(admin.ModelAdmin):
    list_display = ['student_profile', 'exercise', 'status', 'score', 'updated_at']
    list_filter = ['status', 'updated_at']
    search_fields = ['student_profile__user__email', 'exercise__title']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-updated_at']
