"""
Teacher API views
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
import traceback
from django.db import transaction, models
from django.db.models import Q, Count, OuterRef, Subquery, Max
from django.utils import timezone
from datetime import date
from accounts.permissions import IsTeacher
from accounts.models import ImpersonationLog
from rest_framework_simplejwt.tokens import RefreshToken
from students.models import StudentProfile
from groups.models import Group, GroupStudent
from groups.serializers import GroupSerializer
from groups.services import move_student, get_active_students_for_group
from payments.models import Payment
from payments.serializers import PaymentSerializer, PaymentCreateSerializer, TeacherPaymentSerializer
from attendance.models import AttendanceRecord
from coding.models import CodingTask
from django.contrib.auth import get_user_model
from students.serializers import StudentProfileSerializer, StudentProfileUpdateSerializer
from students.models import ParentProfile, ParentChild
from students.credentials import generate_credentials
from core.utils import filter_by_organization, belongs_to_user_organization

User = get_user_model()

def _teacher_owns_group(user, group: Group) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    return getattr(group, "created_by_id", None) == getattr(user, "id", None)

def _resolve_student_user(student_id: int):
    """
    Accept either:
    - User.id (role=student) OR
    - StudentProfile.id (common in teacher UI)
    Returns (user, student_profile_or_none)
    """
    try:
        u = User.objects.get(pk=student_id)
        if getattr(u, "role", None) == "student":
            return u, getattr(u, "student_profile", None)
    except User.DoesNotExist:
        pass
    sp = StudentProfile.objects.select_related("user").filter(pk=student_id, is_deleted=False).first()
    if sp and sp.user and getattr(sp.user, "role", None) == "student":
        return sp.user, sp
    return None, None


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_impersonate_student_view(request, student_id: int):
    """
    POST /api/teacher/impersonate/{student_id}
    Start impersonation as a student (JWT swap) + store impersonator in session.
    """
    # Prevent nested impersonation
    if request.session.get('impersonator_id'):
        return Response({'detail': 'Already impersonating'}, status=status.HTTP_400_BAD_REQUEST)

    student_user, _sp = _resolve_student_user(student_id)
    if not student_user:
        return Response({'detail': 'Student not found'}, status=status.HTTP_404_NOT_FOUND)

    # Only allow impersonation of teacher's own students (group created_by)
    if not request.user.is_superuser:
        owns = GroupStudent.objects.filter(
            active=True,
            left_at__isnull=True,
            group__created_by=request.user,
            student_profile__user_id=student_user.id,
        ).exists()
        if not owns:
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

    # Save original user in session
    request.session['impersonator_id'] = request.user.id
    request.session['impersonated_student_user_id'] = student_user.id

    # Audit log
    ImpersonationLog.objects.create(
        teacher=request.user,
        student=student_user,
        started_at=timezone.now(),
    )

    refresh = RefreshToken.for_user(student_user)
    return Response({
        'accessToken': str(refresh.access_token),
        'user': {
            'email': student_user.email,
            'fullName': student_user.full_name,
            'role': (student_user.role or 'student').lower(),
            'mustChangePassword': getattr(student_user, 'must_change_password', False),
        },
        'detail': 'Impersonation started',
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def teacher_stop_impersonation_view(request):
    """
    POST /api/teacher/stop-impersonation
    Return to original teacher (JWT swap) using session impersonator_id.
    """
    impersonator_id = request.session.get('impersonator_id')
    if not impersonator_id:
        return Response({'detail': 'Not impersonating'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        teacher = User.objects.get(pk=impersonator_id)
    except User.DoesNotExist:
        # Session is stale; clear it and force re-auth on client
        request.session.pop('impersonator_id', None)
        request.session.pop('impersonated_student_user_id', None)
        return Response({'detail': 'Impersonator not found'}, status=status.HTTP_400_BAD_REQUEST)

    if getattr(teacher, "role", None) != "teacher":
        request.session.pop('impersonator_id', None)
        request.session.pop('impersonated_student_user_id', None)
        return Response({'detail': 'Invalid impersonator'}, status=status.HTTP_400_BAD_REQUEST)

    # Close latest open log (best-effort)
    try:
        ImpersonationLog.objects.filter(
            teacher_id=teacher.id,
            ended_at__isnull=True,
        ).order_by('-started_at').update(ended_at=timezone.now())
    except Exception:
        pass

    request.session.pop('impersonator_id', None)
    request.session.pop('impersonated_student_user_id', None)

    refresh = RefreshToken.for_user(teacher)
    return Response({
        'accessToken': str(refresh.access_token),
        'user': {
            'email': teacher.email,
            'fullName': teacher.full_name,
            'role': (teacher.role or 'teacher').lower(),
            'mustChangePassword': getattr(teacher, 'must_change_password', False),
        },
        'detail': 'Returned to teacher',
    }, status=status.HTTP_200_OK)

def _active_students_queryset(request):
    qs = StudentProfile.objects.filter(is_deleted=False, user__is_active=True).select_related('user')
    return filter_by_organization(qs, request.user, 'user__organization')


def _deleted_students_queryset(request):
    qs = StudentProfile.objects.filter(is_deleted=True).select_related('user')
    return filter_by_organization(qs, request.user, 'user__organization')


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_stats_view(request):
    """
    GET /api/teacher/stats
    Get teacher dashboard statistics
    """
    from decimal import Decimal
    total_students = _active_students_queryset(request).count()
    active_students = total_students

    negative_balance_students = _active_students_queryset(request).filter(balance__lt=Decimal('0')).count()
    
    today = date.today()
    today_attendance = AttendanceRecord.objects.filter(
        lesson_date=today,
        status='present'
    ).count()
    
    coding_exercises_count = CodingTask.objects.filter(deleted_at__isnull=True).count()
    
    return Response({
        'totalStudents': total_students,
        'activeStudents': active_students,
        'todayAttendance': today_attendance,
        'codingExercisesCount': coding_exercises_count,
        'negativeBalanceStudents': negative_balance_students,
    })


@api_view(['GET', 'POST', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_students_view(request, pk=None):
    """
    GET /api/teacher/students?status=active|deleted
    POST /api/teacher/students — Create student with auto-generated credentials
    PATCH /api/teacher/students/{id}
    DELETE /api/teacher/students/{id} (soft delete)
    DELETE /api/teacher/students/{id}/hard (hard delete)
    """
    if request.method == 'GET':
        status_filter = request.query_params.get('status', 'active')
        if status_filter == 'deleted':
            students = _deleted_students_queryset(request)
        else:
            students = _active_students_queryset(request)
        # Optional search: filter by full name at database level (case-insensitive)
        search_query = (request.query_params.get('search') or '').strip()
        if search_query:
            students = students.filter(
                Q(user__full_name__icontains=search_query)
            )
        if settings.DEBUG:
            import sys
            print(f'[teacher_students] count={students.count()}, org={getattr(request.user, "organization_id", None)}', file=sys.stderr)
        serializer = StudentProfileSerializer(students, many=True)
        return Response(serializer.data)

    if request.method == 'POST' and pk is None:
        data = request.data.copy()
        full_name = (data.get('fullName') or data.get('full_name') or '').strip()
        grade = (data.get('grade') or data.get('class') or '').strip() or None
        phone = (data.get('phone') or '').strip() or None
        balance = float(data.get('balance', 0))
        if not full_name:
            return Response({'detail': 'fullName is required'}, status=status.HTTP_400_BAD_REQUEST)

        creds = generate_credentials(full_name)
        org = request.user.organization
        for _ in range(5):
            if User.objects.filter(email=creds['student_email']).exists():
                creds = generate_credentials(full_name)
                continue
            break
        if User.objects.filter(email=creds['student_email']).exists():
            return Response({'detail': 'Could not generate unique email. Try again.'}, status=status.HTTP_409_CONFLICT)

        with transaction.atomic():
            student_user = User.objects.create_user(
                email=creds['student_email'],
                password=creds['student_password'],
                full_name=full_name,
                phone=phone,
                role='student',
                is_active=True,
                organization=org,
                must_change_password=True,
            )
            student_profile = StudentProfile.objects.create(
                user=student_user,
                grade=grade,
                balance=balance,
            )
            parent_user = User.objects.create_user(
                email=creds['parent_email'],
                password=creds['parent_password'],
                full_name=f'{full_name} — Valideyn',
                role='parent',
                is_active=True,
                organization=org,
                must_change_password=True,
            )
            ParentProfile.objects.create(user=parent_user)
            ParentChild.objects.create(parent=parent_user, student=student_user)

        result = StudentProfileSerializer(student_profile).data
        result['credentials'] = {
            'studentEmail': creds['student_email'],
            'studentPassword': creds['student_password'],
            'parentEmail': creds['parent_email'],
            'parentPassword': creds['parent_password'],
        }
        return Response(result, status=status.HTTP_201_CREATED)
    
    if request.method == 'PATCH':
        try:
            student = StudentProfile.objects.select_related('user').get(id=pk)
        except StudentProfile.DoesNotExist:
            return Response({'detail': 'Student not found'}, status=status.HTTP_404_NOT_FOUND)
        if not belongs_to_user_organization(student.user, request.user, 'organization'):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

        data = request.data.copy()
        if 'class' in data:
            data['grade'] = data.pop('class')
        serializer = StudentProfileUpdateSerializer(student, data=data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(StudentProfileSerializer(student).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    if request.method == 'DELETE':
        try:
            student = StudentProfile.objects.select_related('user').get(id=pk)
        except StudentProfile.DoesNotExist:
            return Response({'detail': 'Student not found'}, status=status.HTTP_404_NOT_FOUND)
        if not belongs_to_user_organization(student.user, request.user, 'organization'):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

        # Hard delete: remove from groups, delete payments, exam attempts, coding; delete parent if only child
        if request.path.endswith('/hard') or request.path.endswith('/hard/'):
            user = student.user
            with transaction.atomic():
                from tests.models import ExamAttempt
                from attendance.models import AttendanceRecord
                has_history = (
                    ExamAttempt.objects.filter(student=user).exists()
                    or AttendanceRecord.objects.filter(student_profile=student).exists()
                )
                parent_ids = list(
                    ParentChild.objects.filter(student=user).values_list('parent_id', flat=True)
                )
                ParentChild.objects.filter(student=user).delete()
                for parent_id in parent_ids:
                    if not ParentChild.objects.filter(parent_id=parent_id).exists():
                        try:
                            parent_user = User.objects.get(pk=parent_id, role='parent')
                            if hasattr(parent_user, 'parent_profile'):
                                parent_user.parent_profile.delete()
                            parent_user.delete()
                        except (User.DoesNotExist, Exception):
                            pass
                if has_history:
                    # Preserve historical audit rows; anonymize instead of physical delete.
                    user.full_name = "Deleted Student"
                    user.email = f"deleted-student-{user.id}@deleted.local"
                    user.is_active = False
                    user.save(update_fields=['full_name', 'email', 'is_active'])
                    student.deleted_at = timezone.now()
                    student.is_deleted = True
                    student.save(update_fields=['deleted_at', 'is_deleted', 'updated_at'])
                    return Response({'detail': 'Student anonymized to preserve historical records.'}, status=status.HTTP_200_OK)
                user.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        # Soft delete
        from django.utils import timezone
        student.deleted_at = timezone.now()
        student.is_deleted = True
        if student.user and student.user.is_active:
            student.user.is_active = False
            student.user.save(update_fields=['is_active'])
        student.save(update_fields=['deleted_at', 'is_deleted', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_student_restore_view(request, pk):
    """
    POST /api/teacher/students/{id}/restore
    Restore a soft-deleted student. Sets is_deleted=False, deleted_at=null.
    """
    try:
        student = StudentProfile.objects.select_related('user').get(id=pk)
    except StudentProfile.DoesNotExist:
        return Response({'detail': 'Student not found'}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(student.user, request.user, 'organization'):
        return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

    if not student.is_deleted:
        return Response({'detail': 'Student is not deleted'}, status=status.HTTP_400_BAD_REQUEST)

    student.deleted_at = None
    student.is_deleted = False
    student.save(update_fields=['deleted_at', 'is_deleted', 'updated_at'])
    if student.user and not student.user.is_active:
        student.user.is_active = True
        student.user.save(update_fields=['is_active'])

    serializer = StudentProfileSerializer(student)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET', 'POST', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_groups_view(request, pk=None):
    """
    GET /api/teacher/groups
    POST /api/teacher/groups
    PATCH /api/teacher/groups/{id}
    DELETE /api/teacher/groups/{id}
    """
    if request.method == 'GET':
        groups = Group.objects.filter(deleted_at__isnull=True).select_related('organization')
        groups = filter_by_organization(groups, request.user)
        if not request.user.is_superuser:
            groups = groups.filter(created_by=request.user)
        serializer = GroupSerializer(groups, many=True)
        return Response(serializer.data)
    
    if request.method == 'POST':
        serializer = GroupSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    if request.method == 'PATCH':
        try:
            group = Group.objects.get(id=pk)
        except Group.DoesNotExist:
            return Response({'detail': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)
        if not belongs_to_user_organization(group, request.user) or not _teacher_owns_group(request.user, group):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

        serializer = GroupSerializer(group, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    if request.method == 'DELETE':
        try:
            group = Group.objects.get(id=pk)
        except Group.DoesNotExist:
            return Response({'detail': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)
        if not belongs_to_user_organization(group, request.user) or not _teacher_owns_group(request.user, group):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

        # Soft delete (archive)
        group.is_active = False
        group.deleted_at = timezone.now()
        group.save(update_fields=['is_active', 'deleted_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_group_students_view(request, group_id, student_id=None):
    """
    GET /api/teacher/groups/{id}/students (list students in group)
    POST /api/teacher/groups/{id}/students (add students)
    DELETE /api/teacher/groups/{id}/students/{studentId} (remove)
    """
    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({'detail': 'Qrup tapılmadı'}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user) or not _teacher_owns_group(request.user, group):
        return Response({'detail': 'Bu qrupa əlavə etmə icazəniz yoxdur'}, status=status.HTTP_403_FORBIDDEN)
    
    if request.method == 'GET':
        memberships = get_active_students_for_group(group)
        students = [m.student_profile for m in memberships]
        serializer = StudentProfileSerializer(students, many=True)
        return Response(serializer.data)
    
    if request.method == 'POST':
        student_ids = request.data.get('studentIds', [])
        if not isinstance(student_ids, list):
            return Response({'detail': 'studentIds siyahı olmalıdır'}, status=status.HTTP_400_BAD_REQUEST)
        if not student_ids:
            return Response({'detail': 'Əlavə ediləcək şagird seçilməyib'}, status=status.HTTP_400_BAD_REQUEST)
        
        teacher_org = getattr(request.user, 'organization_id', None)
        added = []
        errors = []
        for sid in student_ids:
            try:
                student = StudentProfile.objects.select_related('user').get(id=sid, is_deleted=False)
            except StudentProfile.DoesNotExist:
                errors.append(f'Şagird #{sid} tapılmadı')
                continue
            if teacher_org and getattr(student.user, 'organization_id', None) != teacher_org:
                errors.append(f'{student.user.full_name} sizin təşkilatınıza aid deyil')
                continue
            org_id = student.user.organization_id if hasattr(student.user, 'organization_id') else group.organization_id
            membership, created = GroupStudent.objects.get_or_create(
                group=group,
                student_profile=student,
                defaults={'active': True, 'organization_id': org_id}
            )
            if not created:
                # If already active, treat as conflict (can't add twice)
                if membership.active and membership.left_at is None:
                    errors.append(f'{student.user.full_name} artıq bu qrupdadır')
                    continue
                membership.active = True
                membership.left_at = None
                membership.save(update_fields=['active', 'left_at'])
            added.append(str(sid))
        
        if errors and not added:
            return Response({'detail': '; '.join(errors)}, status=status.HTTP_400_BAD_REQUEST)
        # If some were duplicates, report 409 while still returning added list
        resp = {'added': added, 'errors': errors if errors else None}
        return Response(resp, status=status.HTTP_409_CONFLICT if errors else status.HTTP_200_OK)
    
    if request.method == 'DELETE':
        try:
            student = StudentProfile.objects.get(id=student_id)
            membership = GroupStudent.objects.get(group=group, student_profile=student)
            membership.active = False
            membership.save()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except (StudentProfile.DoesNotExist, GroupStudent.DoesNotExist):
            return Response({'detail': 'Student or membership not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_move_student_view(request):
    """
    POST /api/teacher/groups/move-student
    Move student from one group to another
    """
    student_id = request.data.get('studentId')
    from_group_id = request.data.get('fromGroupId')
    to_group_id = request.data.get('toGroupId')
    
    if not all([student_id, from_group_id, to_group_id]):
        return Response(
            {'detail': 'studentId, fromGroupId, and toGroupId are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        from_group = Group.objects.get(id=from_group_id)
        to_group = Group.objects.get(id=to_group_id)
        if (not belongs_to_user_organization(from_group, request.user) or not _teacher_owns_group(request.user, from_group)
            or not belongs_to_user_organization(to_group, request.user) or not _teacher_owns_group(request.user, to_group)):
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
        move_student(student_id, from_group_id, to_group_id)
        return Response({'detail': 'Student moved successfully'}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_payments_view(request, pk=None):
    """
    GET /api/teacher/payments?groupId=&studentId=
    POST /api/teacher/payments
    DELETE /api/teacher/payments/{id}
    """
    if request.method == 'GET':
        payments = Payment.objects.filter(deleted_at__isnull=True).select_related(
            'student_profile__user', 'group', 'organization'
        )
        payments = filter_by_organization(payments, request.user)
        
        group_id = request.query_params.get('groupId')
        student_id = request.query_params.get('studentId')
        
        if group_id:
            payments = payments.filter(group_id=group_id)
        if student_id:
            payments = payments.filter(student_profile_id=student_id)
        
        serializer = TeacherPaymentSerializer(payments, many=True)
        return Response(serializer.data)
    
    if request.method == 'POST':
        import logging
        logger = logging.getLogger(__name__)
        
        # Pass frontend format directly; PaymentCreateSerializer expects studentId, groupId
        serializer = PaymentCreateSerializer(data=request.data)
        if serializer.is_valid():
            try:
                # Get old balance before creating payment (for logging)
                student_id = request.data.get('studentId')
                old_balance = None
                if student_id:
                    try:
                        from students.models import StudentProfile
                        student_before = StudentProfile.objects.get(id=student_id)
                        old_balance = float(student_before.balance) if student_before.balance else 0.0
                    except StudentProfile.DoesNotExist:
                        pass
                
                payment = serializer.save(created_by=request.user, organization=request.user.organization)
                
                # Refresh payment to get updated student balance
                payment.refresh_from_db()
                student = payment.student_profile
                student.refresh_from_db()
                
                # Return response with updated balance info
                from students.utils import get_teacher_display_balance
                response_data = TeacherPaymentSerializer(payment).data
                response_data['success'] = True
                response_data['message'] = 'Ödəniş əlavə olundu'
                response_data['studentId'] = str(student.id)
                response_data['studentName'] = student.user.full_name
                response_data['newRealBalance'] = float(student.balance)
                response_data['newDisplayBalanceTeacher'] = get_teacher_display_balance(student.balance)
                
                if old_balance is not None:
                    logger.info(f"[PAYMENT] Payment created successfully: payment_id={payment.id}, student_id={student.id}, old_balance={old_balance}, new_balance={float(student.balance)}")
                else:
                    logger.info(f"[PAYMENT] Payment created successfully: payment_id={payment.id}, student_id={student.id}, new_balance={float(student.balance)}")
                
                return Response(response_data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f"[PAYMENT] Error creating payment: {e}", exc_info=True)
                error_detail = str(e)
                return Response(
                    {'detail': f'Ödəniş yaradılarkən xəta baş verdi: {error_detail}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    if request.method == 'DELETE':
        try:
            payment = Payment.objects.get(id=pk)
            if not belongs_to_user_organization(payment, request.user):
                return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
            payment.deleted_at = timezone.now()
            payment.save(update_fields=['deleted_at'])
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Payment.DoesNotExist:
            return Response({'detail': 'Payment not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_notifications_low_balance_view(request):
    """
    GET /api/teacher/notifications/low-balance
    Returns students with real_balance <= 0 for teacher alert list.
    Dynamic: computed from DB, auto-removes after topup.
    Response: { unread_count: int, items: [{studentId, fullName, grade, displayBalanceTeacher, realBalance, reason}] }
    """
    from decimal import Decimal
    from django.db.models import OuterRef, Subquery
    from students.models import BalanceLedger
    from groups.models import GroupStudent
    
    qs = StudentProfile.objects.filter(
        is_deleted=False,
        balance__lte=Decimal('0'),
    )
    qs = filter_by_organization(qs, request.user, 'user__organization')

    # Get last lesson charge date from BalanceLedger
    last_debit = BalanceLedger.objects.filter(
        student_profile=OuterRef('pk'),
        reason=BalanceLedger.REASON_LESSON_CHARGE,
    ).order_by('-date').values('date')[:1]

    # Get first active group
    first_group = GroupStudent.objects.filter(
        student_profile=OuterRef('pk'),
        active=True,
        left_at__isnull=True,
    ).order_by('joined_at').values('group_id')[:1]

    qs = qs.annotate(
        last_lesson_date=Subquery(last_debit),
        first_group_id=Subquery(first_group),
    ).select_related('user')

    group_ids = list(qs.values_list('first_group_id', flat=True))
    group_ids = [g for g in group_ids if g is not None]
    groups_map = {}
    if group_ids:
        for g in Group.objects.filter(id__in=group_ids).values('id', 'name'):
            groups_map[g['id']] = g['name']

    items = []
    for sp in qs:
        balance_real = float(sp.balance)
        # Double-check: only include students with balance <= 0
        if balance_real > 0:
            continue
        items.append({
            'studentId': str(sp.id),
            'fullName': sp.user.full_name,
            'grade': sp.grade or '',
            'displayBalanceTeacher': round(balance_real / 4, 2),
            'realBalance': balance_real,
            'reason': 'BALANCE_ZERO',
            'groupId': str(sp.first_group_id) if sp.first_group_id else None,
            'groupName': groups_map.get(sp.first_group_id, ''),
            'lastLessonDate': sp.last_lesson_date.isoformat() if sp.last_lesson_date else None,
        })
    
    return Response({
        'unread_count': len(items),
        'items': items,
    })
