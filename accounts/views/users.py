"""
Users Management API — Teacher-only.
Stable JSON contract: list (paginated), create, patch, soft_delete, restore.
"""
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Prefetch
from django.utils import timezone

from accounts.permissions import IsTeacher
from accounts.models import User
from students.models import StudentProfile, ParentProfile, TeacherProfile
from core.utils import filter_by_organization


class UsersPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def _user_to_dict(u):
    """Convert User to API response shape."""
    grade = None
    is_deleted = False
    if hasattr(u, 'student_profile'):
        sp = u.student_profile
        grade = sp.grade
        is_deleted = sp.deleted_at is not None
    return {
        'id': str(u.id),
        'email': u.email,
        'fullName': u.full_name,
        'role': u.role,
        'grade': grade,
        'phone': u.phone or None,
        'isDeleted': is_deleted,
        'createdAt': u.date_joined.isoformat() if u.date_joined else None,
    }


def _users_queryset(request, role_filter=None, status_filter='active'):
    """Return users for teacher. Single-tenant: show all users (no org filter)."""
    if getattr(settings, 'SINGLE_TENANT', True):
        qs = User.objects.all().order_by('-date_joined')
    else:
        org = request.user.organization
        if org is None:
            qs = User.objects.filter(organization__isnull=True).order_by('-date_joined')
        else:
            qs = User.objects.filter(organization=org).order_by('-date_joined')

    if role_filter:
        qs = qs.filter(role=role_filter)

    if status_filter == 'deleted':
        qs = qs.filter(student_profile__deleted_at__isnull=False)
    elif status_filter == 'active':
        qs = qs.filter(
            Q(role='teacher') | Q(role='parent') |
            Q(role='student', student_profile__deleted_at__isnull=True)
        )

    qs = qs.select_related('student_profile', 'parent_profile', 'teacher_profile').distinct()
    return qs


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def users_list_or_create_view(request):
    """GET = list, POST = create"""
    if request.method == 'GET':
        return users_list_view(request)
    return users_create_view(request)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def users_list_view(request):
    """
    GET /api/users/
    Query params: page, page_size, role (teacher|student|parent), status (active|deleted|all), search, ordering
    """
    role_filter = request.query_params.get('role')
    if role_filter and role_filter not in ('teacher', 'student', 'parent'):
        role_filter = None

    status_filter = request.query_params.get('status', 'active')
    if status_filter not in ('active', 'deleted', 'all'):
        status_filter = 'active'

    search = (request.query_params.get('search') or '').strip()
    ordering = request.query_params.get('ordering', '-date_joined')
    allowed_ordering = ('full_name', '-full_name', 'date_joined', '-date_joined', 'created_at', '-created_at')
    if ordering.lstrip('-') not in ('full_name', 'date_joined', 'created_at'):
        ordering = '-date_joined'

    qs = _users_queryset(request, role_filter=role_filter, status_filter=status_filter)

    if status_filter == 'all':
        if getattr(settings, 'SINGLE_TENANT', True):
            qs = User.objects.all().order_by('-date_joined')
        else:
            qs = User.objects.filter(organization=request.user.organization).order_by('-date_joined')
        if role_filter:
            qs = qs.filter(role=role_filter)
        qs = qs.select_related('student_profile', 'parent_profile', 'teacher_profile').distinct()

    if search:
        qs = qs.filter(
            Q(email__icontains=search) |
            Q(full_name__icontains=search)
        )

    qs = qs.order_by(ordering)

    paginator = UsersPagination()
    page = paginator.paginate_queryset(qs, request)
    results = [_user_to_dict(u) for u in page]
    return paginator.get_paginated_response(results)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def users_create_view(request):
    """
    POST /api/users/
    Body: email, password, fullName, role, grade (if student), phone, parentEmail, parentPassword (optional)
    """
    from students.credentials import generate_credentials, generate_parent_credentials
    from students.models import ParentChild
    from django.db import transaction

    data = request.data
    email = (data.get('email') or '').strip()
    password = (data.get('password') or '').strip()
    full_name = (data.get('fullName') or data.get('full_name') or '').strip()
    role = (data.get('role') or 'student').lower()
    grade = (data.get('grade') or data.get('class') or '').strip() or None
    phone = (data.get('phone') or '').strip() or None

    if role not in ('teacher', 'student', 'parent'):
        return Response({'detail': 'role must be teacher, student, or parent'}, status=status.HTTP_400_BAD_REQUEST)

    if not full_name:
        return Response({'detail': 'fullName is required'}, status=status.HTTP_400_BAD_REQUEST)

    org = request.user.organization

    if role == 'student':
        if not email or not password:
            creds = generate_credentials(full_name)
            email = creds['student_email']
            password = creds['student_password']
        if User.objects.filter(email=email).exists():
            return Response({'detail': 'Email already exists'}, status=status.HTTP_409_CONFLICT)
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=password,
                full_name=full_name,
                phone=phone,
                role='student',
                is_active=True,
                organization=org,
                must_change_password=True,
            )
            StudentProfile.objects.create(user=user, grade=grade, balance=0)
            create_parent = bool((data.get('parentEmail') or '').strip() or (data.get('parentPassword') or '').strip())
            if create_parent:
                pwd = (data.get('parentPassword') or '').strip() or generate_credentials(full_name)['parent_password']
                pemail = (data.get('parentEmail') or '').strip() or generate_parent_credentials(full_name)[0]
                for _ in range(5):
                    if not User.objects.filter(email=pemail).exists():
                        break
                    pemail, pwd = generate_parent_credentials(full_name)
                parent_user = User.objects.create_user(
                    email=pemail,
                    password=pwd,
                    full_name=f'{full_name} — Valideyn',
                    role='parent',
                    is_active=True,
                    organization=org,
                    must_change_password=True,
                )
                ParentProfile.objects.create(user=parent_user)
                ParentChild.objects.create(parent=parent_user, student=user)
        return Response(_user_to_dict(user), status=status.HTTP_201_CREATED)

    elif role == 'parent':
        if not email or not password:
            pemail, pwd = generate_parent_credentials(full_name)
            email = pemail
            password = pwd
        if User.objects.filter(email=email).exists():
            return Response({'detail': 'Email already exists'}, status=status.HTTP_409_CONFLICT)
        user = User.objects.create_user(
            email=email,
            password=password,
            full_name=full_name,
            phone=phone,
            role='parent',
            is_active=True,
            organization=org,
            must_change_password=True,
        )
        ParentProfile.objects.create(user=user)
        return Response(_user_to_dict(user), status=status.HTTP_201_CREATED)

    else:
        if not email or not password:
            return Response({'detail': 'email and password required for teacher'}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(email=email).exists():
            return Response({'detail': 'Email already exists'}, status=status.HTTP_409_CONFLICT)
        user = User.objects.create_user(
            email=email,
            password=password,
            full_name=full_name,
            phone=phone,
            role='teacher',
            is_active=True,
            organization=org,
            must_change_password=True,
        )
        TeacherProfile.objects.create(user=user)
        return Response(_user_to_dict(user), status=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsTeacher])
def users_update_view(request, pk):
    """PATCH /api/users/{id}/"""
    try:
        user = User.objects.select_related('student_profile').get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = request.user.organization
        if user.organization_id != (org.id if org else None):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    if 'fullName' in data or 'full_name' in data:
        user.full_name = (data.get('fullName') or data.get('full_name') or user.full_name).strip()
    if 'phone' in data:
        user.phone = (data.get('phone') or '').strip() or None
    if 'grade' in data or 'class' in data:
        grade = (data.get('grade') or data.get('class') or '').strip() or None
        if user.role == 'student' and hasattr(user, 'student_profile'):
            user.student_profile.grade = grade
            user.student_profile.save()
    if 'role' in data:
        return Response({'detail': 'Role change is not allowed'}, status=status.HTTP_400_BAD_REQUEST)
    user.save()
    user.refresh_from_db()
    if hasattr(user, 'student_profile'):
        user.student_profile.refresh_from_db()
    return Response(_user_to_dict(user))


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def users_soft_delete_view(request, pk):
    """POST /api/users/{id}/soft_delete — Soft delete (student: set deleted_at on profile)."""
    try:
        user = User.objects.select_related('student_profile').get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = request.user.organization
        if user.organization_id != (org.id if org else None):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

    if user.role == 'student' and hasattr(user, 'student_profile'):
        user.student_profile.deleted_at = timezone.now()
        user.student_profile.save()
        return Response(_user_to_dict(user))
    return Response({'detail': 'Soft delete only applies to students'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def users_restore_view(request, pk):
    """POST /api/users/{id}/restore — Restore soft-deleted student."""
    try:
        user = User.objects.select_related('student_profile').get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = request.user.organization
        if user.organization_id != (org.id if org else None):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

    if user.role == 'student' and hasattr(user, 'student_profile'):
        user.student_profile.deleted_at = None
        user.student_profile.save()
        return Response(_user_to_dict(user))
    return Response({'detail': 'Restore only applies to students'}, status=status.HTTP_400_BAD_REQUEST)
