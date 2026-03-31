"""
Student API URLs.
Mounted at: /api/student/
Example: GET /api/student/runs/<run_id>/pages -> student_run_pages_view (page image URLs).
"""
from django.urls import path
from ..views.student import (
    student_stats_view,
    student_attendance_view,
    student_results_view,
    student_coding_view,
    student_coding_detail_view,
    student_coding_submissions_view,
    student_coding_submission_detail_view,
    student_coding_run_view,
    student_coding_submit_view,
)
from tests.views.exams import (
    student_exams_list_view,
    student_exam_my_results_view,
    student_exam_start_view,
    student_run_start_view,
    student_run_pdf_view,
    student_run_pages_view,
    student_exam_submit_view,
    student_exam_result_view,
    student_exam_canvas_save_view,
    student_pdf_scribbles_view,
    student_exam_suspend_view,
    student_exam_attempt_sync_view,
)

app_name = 'student'

urlpatterns = [
    path('stats', student_stats_view, name='stats'),
    path('attendance', student_attendance_view, name='attendance'),
    path('results', student_results_view, name='results'),
    path('coding', student_coding_view, name='coding'),
    path('coding/run', student_coding_run_view, name='coding-run'),
    path('coding/<int:pk>', student_coding_detail_view, name='coding-detail'),
    path('coding/<int:pk>/submissions', student_coding_submissions_view, name='coding-submissions'),
    path('coding/<int:pk>/submissions/<int:submission_id>', student_coding_submission_detail_view, name='coding-submission-detail'),
    path('coding/<int:pk>/submit', student_coding_submit_view, name='coding-submit'),
    path('exams', student_exams_list_view, name='exams'),
    path('exams/my-results', student_exam_my_results_view, name='exam-my-results'),
    path('runs/<int:run_id>/start', student_run_start_view, name='run-start'),
    path('runs/<int:run_id>/pdf', student_run_pdf_view, name='run-pdf'),
    path('runs/<int:run_id>/pages', student_run_pages_view, name='run-pages'),
    path('runs/<int:run_id>/pages/', student_run_pages_view, name='run-pages-slash'),
    path('exams/<int:exam_id>/start', student_exam_start_view, name='exam-start'),
    path('exams/<int:exam_id>/submit', student_exam_submit_view, name='exam-submit'),
    path('exams/attempts/<int:attempt_id>/sync', student_exam_attempt_sync_view, name='exam-attempt-sync'),
    path('exams/attempts/<int:attempt_id>/canvas', student_exam_canvas_save_view, name='exam-canvas-save'),
    path('exams/attempts/<int:attempt_id>/pdf-scribbles', student_pdf_scribbles_view, name='exam-pdf-scribbles'),
    path('exams/suspend', student_exam_suspend_view, name='exam-suspend'),
    path('exams/<int:exam_id>/attempts/<int:attempt_id>/result', student_exam_result_view, name='exam-result'),
]
