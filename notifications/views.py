"""
Notification views for teacher.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from notifications.models import Notification
from notifications.serializers import NotificationSerializer
from core.utils import filter_by_organization, belongs_to_user_organization


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def teacher_notifications_view(request):
    """
    GET /api/teacher/notifications
    Returns active (unresolved) notifications for teacher's organization.
    """
    from accounts.permissions import IsTeacher
    
    if not IsTeacher().has_permission(request, None):
        return Response({"detail": "Teacher access required"}, status=status.HTTP_403_FORBIDDEN)
    
    # Get notifications for students in teacher's organization (active = is_read=False)
    qs = Notification.objects.filter(
        is_read=False,
    ).select_related('student__user', 'group')
    
    # Filter by organization
    if request.user.organization:
        qs = qs.filter(student__user__organization=request.user.organization)
    
    # Order by created_at desc
    qs = qs.order_by('-created_at')
    
    serializer = NotificationSerializer(qs, many=True)
    
    unread_count = qs.count()  # All active notifications are unread
    
    return Response({
        'notifications': serializer.data,
        'unread_count': unread_count,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def teacher_notifications_count_view(request):
    """
    GET /api/teacher/notifications/count
    Returns unread notification count only (for header badge).
    """
    from accounts.permissions import IsTeacher
    
    if not IsTeacher().has_permission(request, None):
        return Response({"count": 0})
    
    qs = Notification.objects.filter(is_read=False)
    
    # Filter by organization
    if request.user.organization:
        qs = qs.filter(student__user__organization=request.user.organization)
    
    count = qs.count()
    
    return Response({'count': count})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def teacher_notification_mark_read_view(request, notification_id):
    """
    POST /api/teacher/notifications/{id}/read
    Mark notification as read.
    """
    from accounts.permissions import IsTeacher
    
    if not IsTeacher().has_permission(request, None):
        return Response({"detail": "Teacher access required"}, status=status.HTTP_403_FORBIDDEN)
    
    try:
        notification = Notification.objects.get(id=notification_id)
    except Notification.DoesNotExist:
        return Response({"detail": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)
    
    # Check organization
    if notification.student and notification.student.user.organization != request.user.organization:
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
    
    notification.is_read = True
    notification.save(update_fields=['is_read'])
    
    return Response({"detail": "Marked as read"})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def teacher_notification_resolve_view(request, notification_id):
    """
    POST /api/teacher/notifications/{id}/resolve
    Mark notification as resolved.
    """
    from accounts.permissions import IsTeacher
    
    if not IsTeacher().has_permission(request, None):
        return Response({"detail": "Teacher access required"}, status=status.HTTP_403_FORBIDDEN)
    
    try:
        notification = Notification.objects.get(id=notification_id)
    except Notification.DoesNotExist:
        return Response({"detail": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)
    
    # Check organization
    if notification.student and notification.student.user.organization != request.user.organization:
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
    
    notification.is_resolved = True
    notification.resolved_at = timezone.now()
    notification.save(update_fields=['is_resolved', 'resolved_at'])
    
    return Response({"detail": "Resolved"})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def teacher_notifications_mark_all_read_view(request):
    """
    POST /api/teacher/notifications/mark-all-read
    Mark all notifications as read.
    """
    from accounts.permissions import IsTeacher
    
    if not IsTeacher().has_permission(request, None):
        return Response({"detail": "Teacher access required"}, status=status.HTTP_403_FORBIDDEN)
    
    qs = Notification.objects.filter(is_resolved=False)
    
    # Filter by organization
    if request.user.organization:
        qs = qs.filter(student__user__organization=request.user.organization)
    
    updated = qs.filter(is_read=False).update(is_read=True)
    
    return Response({"detail": f"Marked {updated} notifications as read"})
