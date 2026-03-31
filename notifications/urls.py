"""
URLs for notifications app.
"""
from django.urls import path
from notifications.views import (
    teacher_notifications_view,
    teacher_notification_mark_read_view,
    teacher_notification_resolve_view,
    teacher_notifications_mark_all_read_view,
    teacher_notifications_count_view,
)

urlpatterns = [
    path('', teacher_notifications_view, name='notifications-list'),
    path('count/', teacher_notifications_count_view, name='notifications-count'),
    path('<int:notification_id>/read/', teacher_notification_mark_read_view, name='notification-mark-read'),
    path('<int:notification_id>/resolve/', teacher_notification_resolve_view, name='notification-resolve'),
    path('mark-all-read/', teacher_notifications_mark_all_read_view, name='notifications-mark-all-read'),
]
