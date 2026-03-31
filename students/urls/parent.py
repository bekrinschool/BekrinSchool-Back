"""
Parent API URLs
"""
from django.urls import path
from ..views.parent import (
    parent_children_view,
    parent_attendance_view,
    parent_attendance_monthly_view,
    parent_payments_view,
    parent_test_results_view,
    parent_exam_results_view,
    parent_exam_attempt_detail_view,
)

app_name = 'parent'

urlpatterns = [
    path('children', parent_children_view, name='children'),
    path('attendance', parent_attendance_view, name='attendance'),
    path('attendance/monthly', parent_attendance_monthly_view, name='attendance-monthly'),
    path('payments', parent_payments_view, name='payments'),
    path('test-results', parent_test_results_view, name='test-results'),
    path('exam-results', parent_exam_results_view, name='exam-results'),
    path('exams/<int:exam_id>/attempts/<int:attempt_id>/detail', parent_exam_attempt_detail_view, name='exam-attempt-detail'),
]
