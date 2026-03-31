"""
Archive API: list archived payments, groups, students; restore.
"""
from django.db import models
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone

from accounts.permissions import IsTeacher
from core.utils import filter_by_organization, belongs_to_user_organization
from payments.models import Payment
from payments.serializers import TeacherPaymentSerializer
from groups.models import Group
from groups.serializers import GroupSerializer
from students.models import StudentProfile
from students.serializers import StudentProfileSerializer


def _paginate(qs, request, page_size=20):
    page = int(request.query_params.get('page', 1))
    page_size = min(int(request.query_params.get('page_size', page_size)), 100)
    offset = (page - 1) * page_size
    items = list(qs[offset:offset + page_size + 1])
    has_next = len(items) > page_size
    if has_next:
        items = items[:page_size]
    return items, {'page': page, 'page_size': page_size, 'has_next': has_next}


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_payments_view(request):
    """List archived (soft-deleted) payments."""
    q = request.query_params.get('q', '').strip()
    qs = Payment.objects.filter(deleted_at__isnull=False).select_related(
        'student_profile__user', 'group', 'organization'
    ).order_by('-deleted_at', '-payment_date')
    qs = filter_by_organization(qs, request.user)
    if q:
        qs = qs.filter(
            models.Q(student_profile__user__full_name__icontains=q) |
            models.Q(receipt_no__icontains=q) |
            models.Q(title__icontains=q)
        )
    items, meta = _paginate(qs, request)
    return Response({
        'items': TeacherPaymentSerializer(items, many=True).data,
        'meta': meta,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_groups_view(request):
    """List archived (soft-deleted) groups."""
    q = request.query_params.get('q', '').strip()
    qs = Group.objects.filter(deleted_at__isnull=False).select_related('organization').order_by('-deleted_at', 'name')
    qs = filter_by_organization(qs, request.user)
    if q:
        qs = qs.filter(models.Q(name__icontains=q) | models.Q(display_name__icontains=q))
    items, meta = _paginate(qs, request)
    return Response({
        'items': GroupSerializer(items, many=True).data,
        'meta': meta,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_students_view(request):
    """List archived (soft-deleted) students."""
    q = request.query_params.get('q', '').strip()
    qs = StudentProfile.objects.filter(is_deleted=True).select_related('user').order_by('-deleted_at', 'user__full_name')
    qs = filter_by_organization(qs, request.user, 'user__organization')
    if q:
        qs = qs.filter(user__full_name__icontains=q)
    items, meta = _paginate(qs, request)
    return Response({
        'items': StudentProfileSerializer(items, many=True).data,
        'meta': meta,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_payment_view(request, pk):
    """Restore a soft-deleted payment."""
    try:
        payment = Payment.objects.get(id=pk)
    except Payment.DoesNotExist:
        return Response({'detail': 'Ödəniş tapılmadı'}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(payment, request.user):
        return Response({'detail': 'Erişim qadağandır'}, status=status.HTTP_403_FORBIDDEN)
    if payment.deleted_at is None:
        return Response({'detail': 'Ödəniş artıq arxivdə deyil'}, status=status.HTTP_400_BAD_REQUEST)
    payment.deleted_at = None
    payment.save(update_fields=['deleted_at'])
    return Response(TeacherPaymentSerializer(payment).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_group_view(request, pk):
    """Restore a soft-deleted group."""
    try:
        group = Group.objects.get(id=pk)
    except Group.DoesNotExist:
        return Response({'detail': 'Qrup tapılmadı'}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user):
        return Response({'detail': 'Erişim qadağandır'}, status=status.HTTP_403_FORBIDDEN)
    if group.deleted_at is None:
        return Response({'detail': 'Qrup artıq arxivdə deyil'}, status=status.HTTP_400_BAD_REQUEST)
    group.deleted_at = None
    group.is_active = True
    group.save(update_fields=['deleted_at', 'is_active'])
    return Response(GroupSerializer(group).data)
