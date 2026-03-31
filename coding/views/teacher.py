"""
Teacher coding tasks API
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.db.models import Count, Q, Max
from django.db.models.functions import Coalesce
from accounts.permissions import IsTeacher
from students.serializers import StudentProfileSerializer
from coding.models import CodingTask, CodingTestCase, CodingProgress, CodingSubmission, CodingTopic
from coding.serializers import (
    CodingTaskSerializer,
    CodingTaskCreateSerializer,
    CodingTestCaseSerializer,
    CodingTestCaseCreateSerializer,
    CodingTopicSerializer,
    CodingTopicCreateSerializer,
)

# --- JSON Import/Export schema (same format for round-trip) ---
# Each task: title, description, initial_code, difficulty (Easy|Medium|Hard), test_cases
# Each test case: input, expected_output, is_hidden (bool)
DIFFICULTY_NORMALIZE = {'easy': 'easy', 'Easy': 'easy', 'medium': 'medium', 'Medium': 'medium', 'hard': 'hard', 'Hard': 'hard'}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_topics_view(request):
    """GET /api/teacher/coding/topics - list topics. POST - create topic."""
    if request.method == 'GET':
        topics = CodingTopic.objects.filter(is_archived=False).order_by('name')
        serializer = CodingTopicSerializer(topics, many=True)
        return Response(serializer.data)
    if request.method == 'POST':
        serializer = CodingTopicCreateSerializer(data=request.data)
        if serializer.is_valid():
            topic = serializer.save()
            return Response(CodingTopicSerializer(topic).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_topic_delete_view(request, pk):
    """Archive coding topic (soft delete)."""
    try:
        topic = CodingTopic.objects.get(pk=pk)
    except CodingTopic.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    topic.is_archived = True
    topic.save(update_fields=['is_archived'])
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_list_view(request):
    """
    GET /api/teacher/coding?topic_id=&q=&archived= - list coding tasks (paginated)
    POST /api/teacher/coding - create task
    topic_id: filter by topic; q: search title/description; archived: true to include archived.
    """
    if request.method == 'GET':
        tasks_qs = CodingTask.objects.filter(deleted_at__isnull=True).select_related('topic')
        topic_id = request.query_params.get('topic_id', '').strip()
        if topic_id:
            try:
                tasks_qs = tasks_qs.filter(topic_id=int(topic_id))
            except ValueError:
                pass
        include_archived = request.query_params.get('archived', '').lower() in ('1', 'true', 'yes')
        if not include_archived:
            tasks_qs = tasks_qs.filter(is_archived=False)
        q = (request.query_params.get('q') or '').strip()
        if q:
            tasks_qs = tasks_qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        tasks = tasks_qs.order_by('order_index', 'title')
        serializer = CodingTaskSerializer(tasks, many=True)
        return Response(serializer.data)

    if request.method == 'POST':
        serializer = CodingTaskCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            task = serializer.save(created_by=request.user)
            return Response(CodingTaskSerializer(task).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_detail_view(request, pk):
    """
    GET /api/teacher/coding/{id}
    PATCH /api/teacher/coding/{id}
    DELETE /api/teacher/coding/{id} (soft delete)
    """
    try:
        task = CodingTask.objects.select_related('topic').get(id=pk)
    except CodingTask.DoesNotExist:
        return Response({'detail': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = CodingTaskSerializer(task)
        return Response(serializer.data)

    if request.method == 'PATCH':
        serializer = CodingTaskCreateSerializer(task, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(CodingTaskSerializer(task).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        task.is_archived = True
        task.save(update_fields=['is_archived'])
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_monitor_view(request):
    """
    GET /api/teacher/coding-monitor?groupId=&topic=&page=&sort=&include_run=
    Returns: ranking with total_tasks_solved, total_attempts, per_task stats; paginated submissions.
    sort: most_solved | most_attempts | last_activity (default last_activity)
    include_run: true to show RUN submissions (default SUBMIT only)

    Validation:
    - groupId/topic: 400 if non-integer, 404 if group not found (when groupId given)
    - Null-safe: task/student/profile/user can be missing; defensively handled.
    """
    from collections import defaultdict
    from datetime import datetime
    from django.conf import settings
    from groups.models import Group, GroupStudent
    from students.models import StudentProfile

    # ---- Parse & validate query params ----
    group_id_raw = (request.query_params.get('groupId') or '').strip()
    topic_id_raw = (request.query_params.get('topic') or '').strip()
    search = (request.query_params.get('search') or '').strip()
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(50, max(1, int(request.query_params.get('page_size', 20))))
    except (TypeError, ValueError):
        page_size = 20
    sort = (request.query_params.get('sort') or 'last_activity').strip() or 'last_activity'
    include_run = request.query_params.get('include_run', '').lower() in ('1', 'true', 'yes')

    # Validate groupId: must be integer if provided
    group_id = None
    if group_id_raw:
        try:
            group_id = int(group_id_raw)
        except (TypeError, ValueError):
            return Response({'detail': 'groupId must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        # Check group exists; in multi-tenant, optionally restrict to teacher org
        try:
            qs = Group.objects.filter(pk=group_id)
            if not getattr(settings, 'SINGLE_TENANT', True):
                teacher_org = getattr(request.user, 'organization_id', None)
                if teacher_org is not None:
                    qs = qs.filter(organization_id=teacher_org)
                elif getattr(request.user, 'created_groups', None) is not None:
                    qs = qs.filter(created_by=request.user)
            qs.get()
        except Group.DoesNotExist:
            return Response({'detail': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)

    # Validate topic: must be integer if provided
    topic_id = None
    if topic_id_raw:
        try:
            topic_id = int(topic_id_raw)
        except (TypeError, ValueError):
            return Response({'detail': 'topic must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

    # ---- Base queryset: null-safe for task/student ----
    submissions_qs = CodingSubmission.objects.filter(
        task__deleted_at__isnull=True,
        is_archived=False,
    ).select_related('task', 'student')
    if not include_run:
        submissions_qs = submissions_qs.filter(run_type='SUBMIT')
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = getattr(request.user, 'organization_id', None)
        if org is not None:
            submissions_qs = submissions_qs.filter(organization_id=org)
    if topic_id is not None:
        submissions_qs = submissions_qs.filter(task__topic_id=topic_id)

    # Restrict to students in group if groupId given
    if group_id is not None:
        student_user_ids = set(
            GroupStudent.objects.filter(
                group_id=group_id,
                active=True,
                left_at__isnull=True
            ).values_list('student_profile__user_id', flat=True)
        )
        student_user_ids = {x for x in student_user_ids if x is not None}
        if not student_user_ids:
            return Response({
                'ranking': [],
                'submissions': {'count': 0, 'next': None, 'previous': None, 'results': []},
            })
        submissions_qs = submissions_qs.filter(student_id__in=student_user_ids)

    if search:
        submissions_qs = submissions_qs.filter(
            Q(student__full_name__icontains=search) |
            Q(student__email__icontains=search)
        )

    student_ids = list(submissions_qs.values_list('student_id', flat=True).distinct())
    student_ids = [x for x in student_ids if x is not None]
    if not student_ids:
        return Response({
            'ranking': [],
            'submissions': {'count': 0, 'next': None, 'previous': None, 'results': []},
        })

    # ---- Aggregates (null-safe) ----
    passed_by_student = list(
        submissions_qs.filter(status='passed')
        .values('student_id', 'task_id')
        .distinct()
    )
    passed_pairs = {(r['student_id'], r['task_id']) for r in passed_by_student if r.get('student_id') is not None and r.get('task_id') is not None}
    tasks_solved = defaultdict(int)
    for row in passed_by_student:
        sid = row.get('student_id')
        if sid is not None:
            tasks_solved[sid] += 1

    attempts_by_student = (
        submissions_qs.values('student_id')
        .annotate(total_attempts=Count('id'))
    )
    attempt_counts = {r['student_id']: r['total_attempts'] for r in attempts_by_student if r.get('student_id') is not None}

    per_task = (
        submissions_qs.values('student_id', 'task_id', 'task__title')
        .annotate(attempt_count=Count('id'), last_ts=Max('created_at'))
    )
    per_task_map = defaultdict(dict)
    per_task_detail = defaultdict(dict)
    for row in per_task:
        sid = row.get('student_id')
        tid = row.get('task_id')
        if sid is None:
            continue
        title = row.get('task__title') or (f"task_{tid}" if tid is not None else '')
        per_task_map[sid][title] = row.get('attempt_count', 0)
        if tid is not None:
            last_ts = row.get('last_ts')
            per_task_detail[sid][str(tid)] = {
                'attempts': row.get('attempt_count', 0),
                'solved': (sid, tid) in passed_pairs,
                'last_submitted_at': last_ts.isoformat() if last_ts and hasattr(last_ts, 'isoformat') else None,
            }

    last_sub = list(
        submissions_qs.values('student_id')
        .annotate(last_created=Max('created_at'))
        .values_list('student_id', 'last_created')
    )
    last_submission_by_student = {sid: dt for sid, dt in last_sub if sid is not None}

    # ---- Profiles: null-safe (student may have no StudentProfile, user may be None) ----
    profiles = {}
    for p in StudentProfile.objects.filter(user_id__in=student_ids, is_deleted=False).select_related('user'):
        if p.user_id is not None and getattr(p, 'user', None) is not None:
            profiles[p.user_id] = p

    group_names = defaultdict(list)
    for gs in GroupStudent.objects.filter(
        student_profile__user_id__in=student_ids,
        active=True,
        left_at__isnull=True
    ).select_related('group', 'student_profile'):
        sp = getattr(gs, 'student_profile', None)
        if sp is not None and getattr(sp, 'user_id', None) is not None:
            g = getattr(gs, 'group', None)
            name = g.name if g else ''
            group_names[sp.user_id].append(name)

    # ---- Build ranking: deterministic order (include students without profile) ----
    ranking = []
    for sid in student_ids:
        sp = profiles.get(sid)
        user = getattr(sp, 'user', None) if sp else None
        full_name = getattr(user, 'full_name', '') if user else ''
        if sp is not None:
            try:
                student_data = StudentProfileSerializer(sp).data
            except Exception:
                student_data = {'id': getattr(sp, 'id', 0), 'userId': sid, 'fullName': full_name, 'email': '', 'class': '', 'phone': None, 'balance': 0, 'status': 'active'}
        else:
            student_data = {'id': 0, 'userId': sid, 'fullName': full_name, 'email': '', 'class': '', 'phone': None, 'balance': 0, 'status': 'active'}
        total_solved = tasks_solved.get(sid, 0)
        total_attempts = attempt_counts.get(sid, 0)
        last_act = last_submission_by_student.get(sid)
        group_list = group_names.get(sid, []) or []
        pt_map = per_task_map.get(sid, {})
        pt_detail = per_task_detail.get(sid, {})

        ranking.append({
            'student': student_data,
            'student_id': sid,
            'full_name': full_name or student_data.get('fullName', ''),
            'group_names': group_list,
            'groupName': ', '.join(group_list),
            'totalTasksSolved': total_solved,
            'totalAttempts': total_attempts,
            'perTaskAttemptCount': pt_map,
            'per_task_map': pt_detail,
            'lastActivity': last_act.isoformat() if last_act and hasattr(last_act, 'isoformat') else None,
        })

    # Deterministic sort: nulls last, then secondary keys
    def _sort_key_last_activity(x):
        la = x.get('lastActivity')
        if la and isinstance(la, str):
            try:
                return (0, datetime.fromisoformat(la.replace('Z', '+00:00')).timestamp(), -x.get('student_id', 0))
            except Exception:
                pass
        return (1, 0.0, -x.get('student_id', 0))

    def _sort_key_most_solved(x):
        return (-x.get('totalTasksSolved', 0), _sort_key_last_activity(x)[1], -x.get('student_id', 0))

    def _sort_key_most_attempts(x):
        return (-x.get('totalAttempts', 0), -x.get('totalTasksSolved', 0), -x.get('student_id', 0))

    if sort in ('most_attempts', 'most_submissions'):
        ranking.sort(key=_sort_key_most_attempts)
    elif sort == 'last_activity':
        ranking.sort(key=_sort_key_last_activity)
    else:
        ranking.sort(key=_sort_key_most_solved)

    # ---- Paginate submissions ----
    total_submissions = submissions_qs.count()
    offset = (page - 1) * page_size
    page_submissions = list(submissions_qs.order_by('-created_at')[offset:offset + page_size])
    submissions_data = []
    for s in page_submissions:
        student = getattr(s, 'student', None)
        task = getattr(s, 'task', None)
        created_at = getattr(s, 'created_at', None)
        submissions_data.append({
            'id': s.id,
            'taskTitle': getattr(task, 'title', '') if task else '',
            'taskId': s.task_id,
            'studentName': getattr(student, 'full_name', '') if student else '',
            'studentId': s.student_id,
            'studentEmail': getattr(student, 'email', '') if student else '',
            'status': getattr(s, 'status', '') or '',
            'runType': getattr(s, 'run_type', 'SUBMIT'),
            'score': getattr(s, 'score', None),
            'passedCount': getattr(s, 'passed_count', None),
            'failedCount': getattr(s, 'failed_count', None),
            'totalCount': (
                s.total_count
                if getattr(s, 'total_count', None) is not None
                else ((s.passed_count or 0) + (s.failed_count or 0) or 0)
            ),
            'runtimeMs': getattr(s, 'runtime_ms', None),
            'createdAt': created_at.isoformat() if created_at and hasattr(created_at, 'isoformat') else '',
        })

    return Response({
        'ranking': ranking,
        'submissions': {
            'count': total_submissions,
            'next': page + 1 if offset + len(page_submissions) < total_submissions else None,
            'previous': page - 1 if page > 1 else None,
            'results': submissions_data,
        },
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_testcases_list_view(request, pk):
    """
    GET /api/teacher/coding/{id}/testcases - list test cases for task
    POST /api/teacher/coding/{id}/testcases - create test case
    """
    try:
        task = CodingTask.objects.get(id=pk)
    except CodingTask.DoesNotExist:
        return Response({'detail': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        cases = CodingTestCase.objects.filter(task=task).order_by('order_index', 'id')
        serializer = CodingTestCaseSerializer(cases, many=True)
        return Response(serializer.data)

    if request.method == 'POST':
        serializer = CodingTestCaseCreateSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(task=task)
            return Response(CodingTestCaseSerializer(serializer.instance).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_testcase_detail_view(request, caseId):
    """
    PATCH /api/teacher/coding/testcases/{caseId}
    DELETE /api/teacher/coding/testcases/{caseId}
    """
    try:
        case = CodingTestCase.objects.get(id=caseId)
    except CodingTestCase.DoesNotExist:
        return Response({'detail': 'Test case not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'PATCH':
        serializer = CodingTestCaseCreateSerializer(case, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(CodingTestCaseSerializer(serializer.instance).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        case.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_submissions_list_view(request):
    """
    GET /api/teacher/coding/submissions?taskId=&groupId=&studentId=&page=
    Paginated submissions with filters. For teacher monitor.
    """
    from django.conf import settings
    from groups.models import GroupStudent

    task_id = request.query_params.get('taskId', '').strip()
    group_id = request.query_params.get('groupId', '').strip()
    student_id = request.query_params.get('studentId', '').strip()
    page = max(1, int(request.query_params.get('page', 1)))
    page_size = min(100, max(1, int(request.query_params.get('page_size', 20))))

    submissions_qs = CodingSubmission.objects.filter(
        task__deleted_at__isnull=True
    ).select_related('task', 'task__topic', 'student')
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = getattr(request.user, 'organization_id', None)
        if org:
            submissions_qs = submissions_qs.filter(organization_id=org)
    if task_id:
        submissions_qs = submissions_qs.filter(task_id=task_id)
    if group_id:
        student_user_ids = set(
            GroupStudent.objects.filter(
                group_id=group_id,
                active=True,
                left_at__isnull=True
            ).values_list('student_profile__user_id', flat=True)
        )
        submissions_qs = submissions_qs.filter(student_id__in=student_user_ids or [0])
    if student_id:
        submissions_qs = submissions_qs.filter(student_id=student_id)

    total = submissions_qs.count()
    offset = (page - 1) * page_size
    page_qs = submissions_qs.order_by('-created_at')[offset:offset + page_size]

    results = [
        {
            'id': s.id,
            'taskId': s.task_id,
            'taskTitle': s.task.title,
            'topicName': s.task.topic.name if s.task.topic else None,
            'studentId': s.student_id,
            'studentName': s.student.full_name,
            'status': s.status,
            'score': s.score,
            'passedCount': s.passed_count,
            'failedCount': s.failed_count,
            'attemptNo': s.attempt_no,
            'createdAt': s.created_at.isoformat(),
        }
        for s in page_qs
    ]
    return Response({
        'count': total,
        'page': page,
        'pageSize': page_size,
        'next': page + 1 if offset + len(results) < total else None,
        'previous': page - 1 if page > 1 else None,
        'results': results,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_submission_detail_view(request, pk):
    """
    GET /api/teacher/coding/submissions/{id}
    Full submission detail including details_json (per-test results, including hidden). Teacher-only.
    """
    from django.conf import settings
    try:
        sub = CodingSubmission.objects.select_related('task', 'task__topic', 'student').get(id=pk)
    except CodingSubmission.DoesNotExist:
        return Response({'detail': 'Submission not found'}, status=status.HTTP_404_NOT_FOUND)
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = getattr(request.user, 'organization_id', None)
        if org and sub.organization_id != org:
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
    details = sub.details_json or []
    for d in details:
        d.setdefault('input', '')
        d.setdefault('output', '')
        d.setdefault('expected', '')
    return Response({
        'id': sub.id,
        'taskId': sub.task_id,
        'taskTitle': sub.task.title,
        'topicName': sub.task.topic.name if sub.task.topic else None,
        'studentId': sub.student_id,
        'studentName': sub.student.full_name,
        'studentEmail': sub.student.email,
        'submittedCode': sub.submitted_code,
        'status': sub.status,
        'score': sub.score,
        'passedCount': sub.passed_count,
        'failedCount': sub.failed_count,
        'errorMessage': sub.error_message,
        'runtimeMs': sub.runtime_ms,
        'attemptNo': sub.attempt_no,
        'createdAt': sub.created_at.isoformat(),
        'detailsJson': details,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_student_submissions_view(request, student_id):
    """
    GET /api/teacher/coding-monitor/students/{student_id}/submissions?group_id=&topic=&page=&page_size=&include_run=
    All submissions for a student, paginated. include_run=true to show RUN submissions.
    """
    from django.conf import settings
    from accounts.models import User
    from students.models import StudentProfile
    
    try:
        student = User.objects.get(id=student_id, role='student')
    except User.DoesNotExist:
        return Response({'detail': 'Student not found'}, status=status.HTTP_404_NOT_FOUND)
    
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = getattr(request.user, 'organization_id', None)
        if org and student.organization_id != org:
            return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
    
    group_id = request.query_params.get('group_id', '').strip()
    topic_id = request.query_params.get('topic', '').strip()
    task_id = request.query_params.get('taskId', '').strip()
    include_run = request.query_params.get('include_run', '').lower() in ('1', 'true', 'yes')
    page = max(1, int(request.query_params.get('page', 1)))
    page_size = min(100, max(1, int(request.query_params.get('page_size', 20))))

    submissions_qs = CodingSubmission.objects.filter(
        student=student,
        task__deleted_at__isnull=True,
        is_archived=False,
    ).select_related('task', 'task__topic').order_by('-created_at')
    if not include_run:
        submissions_qs = submissions_qs.filter(run_type='SUBMIT')
    
    if topic_id:
        submissions_qs = submissions_qs.filter(task__topic_id=topic_id)
    if task_id:
        submissions_qs = submissions_qs.filter(task_id=task_id)
    
    total = submissions_qs.count()
    offset = (page - 1) * page_size
    page_qs = submissions_qs[offset : offset + page_size]
    
    submissions_data = [
        {
            'id': s.id,
            'taskId': s.task_id,
            'taskTitle': s.task.title,
            'topicName': s.task.topic.name if s.task.topic else None,
            'submittedCode': s.submitted_code,
            'status': s.status,
            'runType': getattr(s, 'run_type', 'SUBMIT'),
            'passedCount': s.passed_count,
            'totalCount': s.total_count or (s.passed_count or 0) + (s.failed_count or 0) or 0,
            'score': s.score,
            'failedCount': s.failed_count,
            'errorMessage': s.error_message,
            'runtimeMs': s.runtime_ms,
            'attemptNo': s.attempt_no,
            'createdAt': s.created_at.isoformat(),
            'detailsJson': getattr(s, 'details_json', None) or [],
        }
        for s in page_qs
    ]
    
    return Response({
        'studentId': student.id,
        'studentName': student.full_name,
        'submissions': submissions_data,
        'count': total,
        'page': page,
        'pageSize': page_size,
        'next': page + 1 if offset + len(submissions_data) < total else None,
        'previous': page - 1 if page > 1 else None,
    })


def _validate_import_task(task_data, index):
    """Validate a single task dict for import. Returns (None, None) if valid, else (error_message, 400)."""
    if not isinstance(task_data, dict):
        return (f"Tapşırıq {index + 1}: obyekt olmalıdır (dict).", 400)
    title = task_data.get('title')
    if not title or not str(title).strip():
        return (f"Tapşırıq {index + 1}: 'title' tələb olunur və boş ola bilməz.", 400)
    description = task_data.get('description')
    if description is None:
        description = ''
    difficulty_raw = (task_data.get('difficulty') or 'easy')
    difficulty = DIFFICULTY_NORMALIZE.get(difficulty_raw) or 'easy'
    if difficulty not in ('easy', 'medium', 'hard'):
        return (f"Tapşırıq {index + 1}: 'difficulty' Easy, Medium və ya Hard olmalıdır.", 400)
    test_cases = task_data.get('test_cases')
    if not isinstance(test_cases, list):
        return (f"Tapşırıq {index + 1}: 'test_cases' massiv olmalıdır.", 400)
    for i, tc in enumerate(test_cases):
        if not isinstance(tc, dict):
            return (f"Tapşırıq {index + 1}, test {i + 1}: obyekt olmalıdır.", 400)
        if 'input' not in tc and 'expected_output' not in tc:
            pass  # allow empty for optional
        inp = tc.get('input')
        if inp is None:
            inp = ''
        exp = tc.get('expected_output')
        if exp is None:
            exp = ''
        if not str(inp).strip() or not str(exp).strip():
            return (f"Tapşırıq {index + 1}, test {i + 1}: 'input' və 'expected_output' tələb olunur.", 400)
    return (None, None)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_import_json_view(request):
    """
    POST /api/teacher/coding/import-json/
    Body: { "tasks": [ { title, description?, initial_code?, difficulty?, test_cases: [ { input, expected_output, is_hidden? } ] } ], topic_id? }
    All-or-nothing: transaction.atomic. Returns "Successfully imported X tasks" or validation error.
    """
    from django.conf import settings
    data = request.data if isinstance(request.data, dict) else {}
    tasks_payload = data.get('tasks')
    if not isinstance(tasks_payload, list):
        return Response(
            {'detail': 'JSON formatı: { "tasks": [ ... ] } olmalıdır. tasks massivi tələb olunur.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    topic_id = data.get('topic_id')
    if topic_id is not None:
        try:
            topic_id = int(topic_id)
        except (TypeError, ValueError):
            return Response({'detail': 'topic_id tam ədəd olmalıdır.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            CodingTopic.objects.get(pk=topic_id)
        except CodingTopic.DoesNotExist:
            return Response({'detail': 'Mövzu tapılmadı.'}, status=status.HTTP_404_NOT_FOUND)
    else:
        first = CodingTopic.objects.filter(is_archived=False).order_by('name').first()
        topic_id = first.id if first else None
        if topic_id is None:
            return Response(
                {'detail': 'Heç bir mövzu yoxdur. Əvvəlcə mövzu yaradın və ya JSON-da topic_id göndərin.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    for idx, task_data in enumerate(tasks_payload):
        err, _ = _validate_import_task(task_data, idx)
        if err:
            return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)

    org = getattr(request.user, 'organization_id', None)
    try:
        with transaction.atomic():
            created_count = 0
            for idx, task_data in enumerate(tasks_payload):
                title = str(task_data.get('title', '')).strip()
                description = str(task_data.get('description', '')).strip() or title
                initial_code = task_data.get('initial_code')
                starter_code = initial_code if isinstance(initial_code, str) else ''
                difficulty_raw = (task_data.get('difficulty') or 'easy')
                difficulty = DIFFICULTY_NORMALIZE.get(difficulty_raw) or 'easy'
                test_cases = task_data.get('test_cases') or []
                task = CodingTask.objects.create(
                    organization_id=org,
                    topic_id=topic_id,
                    title=title,
                    description=description,
                    starter_code=starter_code,
                    difficulty=difficulty,
                    is_active=True,
                    created_by=request.user,
                )
                created_count += 1
                for order_index, tc in enumerate(test_cases):
                    inp = str(tc.get('input', '')).strip()
                    exp = str(tc.get('expected_output', '')).strip()
                    is_hidden = tc.get('is_hidden', False)
                    is_sample = not bool(is_hidden)
                    CodingTestCase.objects.create(
                        task=task,
                        input_data=inp,
                        expected=exp,
                        order_index=order_index,
                        is_sample=is_sample,
                    )
    except Exception as e:
        return Response(
            {'detail': f'İdxal xətası: {str(e)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response({'message': f'Uğurla {created_count} tapşırıq idxal edildi.', 'imported_count': created_count}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_coding_export_json_view(request):
    """
    GET /api/teacher/coding/export-json/?task_ids=1,2,3
    Returns JSON in the same format as import: { tasks: [ { title, description, initial_code, difficulty, test_cases } ] }
    """
    from django.conf import settings
    task_ids_param = (request.query_params.get('task_ids') or '').strip()
    if not task_ids_param:
        return Response({'detail': 'task_ids parametri tələb olunur (məs: ?task_ids=1,2,3)'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        ids = [int(x) for x in task_ids_param.split(',') if x.strip()]
    except ValueError:
        return Response({'detail': 'task_ids vergüllə ayrılmış tam ədədlər olmalıdır.'}, status=status.HTTP_400_BAD_REQUEST)
    if not ids:
        return Response({'detail': 'Ən azı bir task_id təqdim edin.'}, status=status.HTTP_400_BAD_REQUEST)
    qs = CodingTask.objects.filter(pk__in=ids, deleted_at__isnull=True).prefetch_related('test_cases')
    if not getattr(settings, 'SINGLE_TENANT', True):
        org = getattr(request.user, 'organization_id', None)
        if org is not None:
            qs = qs.filter(organization_id=org)
    tasks_list = []
    for task in qs.order_by('order_index', 'title'):
        cases = []
        for tc in task.test_cases.all().order_by('order_index', 'id'):
            cases.append({
                'input': tc.input_data,
                'expected_output': tc.expected,
                'is_hidden': not tc.is_sample,
            })
        tasks_list.append({
            'title': task.title,
            'description': task.description or '',
            'initial_code': task.starter_code or '',
            'difficulty': task.difficulty.capitalize(),
            'test_cases': cases,
        })
    return Response({'tasks': tasks_list})
