"""
Student API views
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsStudent
from attendance.models import AttendanceRecord
from attendance.serializers import AttendanceRecordSerializer
from tests.models import TestResult
from tests.serializers import TestResultSerializer
from coding.models import CodingTask, CodingProgress, CodingSubmission
from django.db.models import Count


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_stats_view(request):
    """
    GET /api/student/stats
    Get student dashboard stats: missed lessons count and percentage
    """
    try:
        student_profile = request.user.student_profile
    except:
        return Response({'detail': 'Student profile not found'}, status=status.HTTP_404_NOT_FOUND)

    from datetime import date, timedelta
    thirty_days_ago = date.today() - timedelta(days=30)
    records = AttendanceRecord.objects.filter(
        student_profile=student_profile,
        lesson_date__gte=thirty_days_ago
    )
    total = records.count()
    absent = records.filter(status='absent').count()
    missed_count = absent + records.filter(status='late').count()
    percent = int((absent / total * 100)) if total > 0 else 0

    return Response({
        'missedCount': missed_count,
        'absentCount': absent,
        'attendancePercent': 100 - percent if total > 0 else 100,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_attendance_view(request):
    """
    GET /api/student/attendance
    Get student's attendance records
    """
    try:
        student_profile = request.user.student_profile
    except:
        return Response({'detail': 'Student profile not found'}, status=status.HTTP_404_NOT_FOUND)
    
    attendance = AttendanceRecord.objects.filter(
        student_profile=student_profile
    ).select_related('group').order_by('-lesson_date')
    
    serializer = AttendanceRecordSerializer(attendance, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_results_view(request):
    """
    GET /api/student/results
    Get student's test results
    """
    try:
        student_profile = request.user.student_profile
    except:
        return Response({'detail': 'Student profile not found'}, status=status.HTTP_404_NOT_FOUND)
    
    results = TestResult.objects.filter(
        student_profile=student_profile
    ).select_related('group').order_by('-date')
    
    serializer = TestResultSerializer(results, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_coding_view(request):
    """
    GET /api/student/coding?topic=&status=&search=&sort=
    topic: topic id (optional)
    status: all | solved | attempted | not_attempted
    search: search in title (optional)
    sort: newest | most_solved | last_activity
    """
    try:
        student_profile = request.user.student_profile
    except Exception:
        return Response({'detail': 'Student profile not found'}, status=status.HTTP_404_NOT_FOUND)

    from django.conf import settings
    user = request.user
    tasks_qs = CodingTask.objects.filter(
        deleted_at__isnull=True,
        is_active=True,
    )
    if not getattr(settings, 'SINGLE_TENANT', True):
        org_id = getattr(user, 'organization_id', None)
        if org_id:
            tasks_qs = tasks_qs.filter(organization_id=org_id)
    topic_id = request.query_params.get('topic', '').strip()
    if topic_id:
        try:
            tasks_qs = tasks_qs.filter(topic_id=int(topic_id))
        except ValueError:
            pass
    search = (request.query_params.get('search') or '').strip()
    from django.db.models import Q
    if search:
        tasks_qs = tasks_qs.filter(
            Q(title__icontains=search) | Q(description__icontains=search)
        )
    tasks_qs = tasks_qs.select_related('topic')
    task_ids = list(tasks_qs.values_list('id', flat=True))
    if not task_ids:
        return Response([])

    # Submission stats per task for this student
    stats_by_task = {}
    for r in CodingSubmission.objects.filter(
        student_id=user.id,
        task_id__in=task_ids,
    ).values('task_id').annotate(attempt_count=Count('id')):
        stats_by_task[r['task_id']] = r
    last_by_task = {}
    for s in CodingSubmission.objects.filter(
        student_id=user.id,
        task_id__in=task_ids,
    ).order_by('task_id', '-created_at').values('task_id', 'status', 'created_at'):
        if s['task_id'] not in last_by_task:
            last_by_task[s['task_id']] = s
    passed_tasks = set(
        CodingSubmission.objects.filter(
            student_id=user.id,
            task_id__in=task_ids,
            status='passed',
        ).values_list('task_id', flat=True).distinct()
    )

    status_filter = (request.query_params.get('status') or 'all').strip().lower()
    sort = (request.query_params.get('sort') or 'newest').strip().lower()
    if sort not in ('newest', 'most_solved', 'last_activity'):
        sort = 'newest'

    result = []
    for task in tasks_qs:
        st = stats_by_task.get(task.id, {})
        attempt_count = st.get('attempt_count', 0)
        solved = task.id in passed_tasks
        last = last_by_task.get(task.id, {})
        last_status = last.get('status') if last else None
        last_created = last.get('created_at') if last else None
        if status_filter == 'solved' and not solved:
            continue
        if status_filter == 'attempted' and attempt_count == 0:
            continue
        if status_filter == 'not_attempted' and attempt_count > 0:
            continue
        if status_filter == 'completed' and not solved:
            continue
        if status_filter == 'not_completed' and solved:
            continue
        result.append({
            'id': task.id,
            'title': task.title,
            'description': task.description,
            'difficulty': task.difficulty,
            'topicId': task.topic_id,
            'topicName': task.topic.name if task.topic else None,
            'solved': solved,
            'attemptCount': attempt_count,
            'lastSubmissionStatus': last_status,
            'lastSubmissionAt': last_created.isoformat() if last_created else None,
            'createdAt': task.created_at.isoformat() if hasattr(task, 'created_at') and task.created_at else None,
            'score': None,
        })
    if sort == 'newest':
        result.sort(key=lambda x: (x['createdAt'] or ''), reverse=True)
    elif sort == 'most_solved':
        result.sort(key=lambda x: (x['solved'], x['attemptCount']), reverse=True)
    elif sort == 'last_activity':
        result.sort(key=lambda x: (x['lastSubmissionAt'] or ''), reverse=True)
    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_coding_detail_view(request, pk):
    """
    GET /api/student/coding/{id}
    Task details, starter_code, test_case_count (not full test cases).
    """
    try:
        request.user.student_profile
    except Exception:
        return Response({'detail': 'Student profile not found'}, status=status.HTTP_404_NOT_FOUND)
    from django.conf import settings
    tasks_qs = CodingTask.objects.filter(
        id=pk,
        deleted_at__isnull=True,
        is_active=True,
    )
    if not getattr(settings, 'SINGLE_TENANT', True):
        org_id = getattr(request.user, 'organization_id', None)
        if org_id:
            tasks_qs = tasks_qs.filter(organization_id=org_id)
    task = tasks_qs.select_related('topic').first()
    if not task:
        return Response({'detail': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)
    from coding.models import CodingTestCase
    test_case_count = CodingTestCase.objects.filter(task_id=task.id).count()
    return Response({
        'id': task.id,
        'title': task.title,
        'description': task.description,
        'difficulty': task.difficulty,
        'starterCode': task.starter_code or '',
        'topicId': task.topic_id,
        'topicName': task.topic.name if task.topic else None,
        'testCaseCount': test_case_count,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_coding_submissions_view(request, pk):
    """
    GET /api/student/coding/{id}/submissions?page=&page_size=
    Paginated submission history for this task (own only).
    """
    try:
        request.user.student_profile
    except Exception:
        return Response({'detail': 'Student profile not found'}, status=status.HTTP_404_NOT_FOUND)
    from django.conf import settings
    tasks_qs = CodingTask.objects.filter(id=pk, deleted_at__isnull=True)
    if not getattr(settings, 'SINGLE_TENANT', True):
        org_id = getattr(request.user, 'organization_id', None)
        if org_id:
            tasks_qs = tasks_qs.filter(organization_id=org_id)
    if not tasks_qs.exists():
        return Response({'detail': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)
    page = max(1, int(request.query_params.get('page', 1)))
    page_size = min(50, max(1, int(request.query_params.get('page_size', 20))))
    qs = CodingSubmission.objects.filter(
        task_id=pk,
        student_id=request.user.id,
        run_type='SUBMIT',
        is_archived=False,
    ).order_by('-created_at')
    total = qs.count()
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    return Response({
        'count': total,
        'next': page + 1 if offset + len(items) < total else None,
        'previous': page - 1 if page > 1 else None,
        'results': [
            {
                'id': s.id,
                'status': s.status,
                'score': s.score,
                'passedCount': s.passed_count,
                'failedCount': s.failed_count,
                'runtimeMs': s.runtime_ms,
                'attemptNo': s.attempt_no,
                'createdAt': s.created_at.isoformat(),
            }
            for s in items
        ],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_coding_submission_detail_view(request, pk, submission_id):
    """
    GET /api/student/coding/{taskId}/submissions/{submissionId}
    Single submission with code (for View Code modal).
    """
    try:
        request.user.student_profile
    except Exception:
        return Response({'detail': 'Student profile not found'}, status=status.HTTP_404_NOT_FOUND)
    sub = CodingSubmission.objects.filter(
        id=submission_id,
        task_id=pk,
        student_id=request.user.id,
    ).first()
    if not sub:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        'id': sub.id,
        'status': sub.status,
        'score': sub.score,
        'passedCount': sub.passed_count,
        'failedCount': sub.failed_count,
        'runtimeMs': sub.runtime_ms,
        'attemptNo': sub.attempt_no,
        'submittedCode': sub.submitted_code,
        'createdAt': sub.created_at.isoformat(),
    })


def _log_coding_debug(msg, data=None):
    """Temporary debug logging for coding endpoints."""
    import sys
    print(f'[coding] {msg}', data or '', file=sys.stderr)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def student_coding_run_view(request):
    """
    POST /api/student/coding/run
    Body: { "taskId" or "task_id": <id>, "code": "..." }
    Run code against first 2 test cases (by order_index, then is_sample).
    Returns: { status: OK|ERROR, results: [{testCaseId, input, expected, actual, passed}], passedCount, totalCount }
    Optionally saves as submission with run_type=RUN.
    """
    try:
        request.user.student_profile
    except Exception:
        return Response({'error': 'Student profile not found', 'details': {}}, status=status.HTTP_404_NOT_FOUND)
    raw_task_id = request.data.get('task_id') or request.data.get('taskId')
    if raw_task_id is None or raw_task_id == '':
        return Response({'error': 'taskId is required', 'details': {'taskId': 'Missing'}}, status=status.HTTP_400_BAD_REQUEST)
    try:
        task_id = int(raw_task_id)
    except (TypeError, ValueError):
        return Response({'error': 'taskId must be a number', 'details': {'taskId': str(raw_task_id)}}, status=status.HTTP_400_BAD_REQUEST)
    code = (request.data.get('code') or request.data.get('submitted_code') or '').strip()
    if not code:
        return Response({'error': 'Code is required', 'details': {'code': 'Empty'}}, status=status.HTTP_400_BAD_REQUEST)
    from django.conf import settings
    from coding.run_code import run_python_code_timed, check_output_match, validate_code_safe

    ok, msg = validate_code_safe(code)
    if not ok:
        return Response({'error': msg, 'details': {'code': 'Validation failed'}}, status=status.HTTP_400_BAD_REQUEST)

    tasks_qs = CodingTask.objects.filter(id=task_id, deleted_at__isnull=True, is_archived=False)
    if not getattr(settings, 'SINGLE_TENANT', True):
        org_id = getattr(request.user, 'organization_id', None)
        if org_id:
            tasks_qs = tasks_qs.filter(organization_id=org_id)
    task = tasks_qs.first()
    if not task:
        return Response({'error': 'Task not found or archived', 'details': {'taskId': task_id}}, status=status.HTTP_404_NOT_FOUND)
    from coding.models import CodingTestCase

    # First 2 tests: by order_index ASC, then is_sample=True; else first 2
    cases_qs = CodingTestCase.objects.filter(task=task).order_by('order_index', 'id')
    ordered = list(cases_qs)
    sample_first = [c for c in ordered if c.is_sample][:2]
    if len(sample_first) >= 2:
        sample_cases = sample_first
    else:
        sample_cases = ordered[:2]
    if not sample_cases:
        return Response({'error': 'No test cases defined for this task', 'details': {}}, status=status.HTTP_400_BAD_REQUEST)

    run_timeout = 2
    results = []
    passed_count = 0
    for tc in sample_cases:
        stdout, stderr, return_code, _ = run_python_code_timed(code, tc.input_data, timeout_seconds=run_timeout)
        actual = (stdout or '') if return_code == 0 else (stderr or stdout or 'Runtime error')
        if return_code != 0 and return_code != -1:
            actual = (stderr or stdout or 'Runtime error')[:2000]
        elif return_code == -1 and 'Timeout' in (stderr or ''):
            actual = 'Timeout'
        passed = return_code == 0 and check_output_match(stdout, tc.expected)
        if passed:
            passed_count += 1
        results.append({
            'testCaseId': tc.id,
            'input': (tc.input_data or '')[:500],
            'expected': (tc.expected or '')[:500],
            'actual': actual[:2000],
            'passed': passed,
        })
    resp_status = 'OK' if passed_count == len(sample_cases) else 'ERROR'
    # Save RUN as submission for teacher monitor (toggle to show runs)
    CodingSubmission.objects.create(
        organization_id=task.organization_id,
        task=task,
        student=request.user,
        submitted_code=code,
        language='python',
        run_type='RUN',
        passed_count=passed_count,
        total_count=len(sample_cases),
        failed_count=len(sample_cases) - passed_count,
        status='passed' if passed_count == len(sample_cases) else 'failed',
    )
    return Response({
        'status': resp_status,
        'results': results,
        'passedCount': passed_count,
        'totalCount': len(sample_cases),
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def student_coding_submit_view(request, pk):
    """
    POST /api/student/coding/{id}/submit
    Body: { "code": "...", "language": "python" (optional) }
    Run code against ALL test cases, save submission with run_type=SUBMIT.
    Returns: { submissionId, status: ACCEPTED|WRONG_ANSWER|ERROR|TIMEOUT, passedCount, totalCount }
    """
    try:
        request.user.student_profile
    except Exception:
        return Response({'error': 'Student profile not found', 'details': {}}, status=status.HTTP_404_NOT_FOUND)
    from django.conf import settings
    from coding.run_code import run_python_code_timed, check_output_match, validate_code_safe

    tasks_qs = CodingTask.objects.filter(id=pk, deleted_at__isnull=True, is_archived=False)
    if not getattr(settings, 'SINGLE_TENANT', True):
        org_id = getattr(request.user, 'organization_id', None)
        if org_id:
            tasks_qs = tasks_qs.filter(organization_id=org_id)
    task = tasks_qs.first()
    if not task:
        return Response({'error': 'Task not found or archived', 'details': {'taskId': pk}}, status=status.HTTP_404_NOT_FOUND)
    code = (request.data.get('code') or request.data.get('submitted_code') or '').strip()
    if not code:
        return Response({'error': 'Code is required', 'details': {'code': 'Empty'}}, status=status.HTTP_400_BAD_REQUEST)
    ok, msg = validate_code_safe(code)
    if not ok:
        return Response({'error': msg, 'details': {'code': 'Validation failed'}}, status=status.HTTP_400_BAD_REQUEST)
    from coding.models import CodingTestCase
    cases = list(CodingTestCase.objects.filter(task=task).order_by('order_index', 'id'))
    if not cases:
        _log_coding_debug('submit no test cases', pk)
        return Response(
            {'error': 'No test cases defined for this task'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    passed_count = 0
    failed_count = 0
    total_time_ms = 0
    error_message = None
    result_status = 'passed'
    run_timeout = 5
    details_list = []
    for tc in cases:
        stdout, stderr, return_code, elapsed_ms = run_python_code_timed(
            code, tc.input_data, timeout_seconds=run_timeout
        )
        total_time_ms += elapsed_ms
        passed = False
        if return_code == -1 and stderr and 'Timeout' in stderr:
            result_status = 'timeout'
            error_message = 'Execution timeout'
            failed_count += 1
            details_list.append({
                'test_case_id': tc.id, 'is_sample': tc.is_sample, 'passed': False,
                'input': (tc.input_data or '')[:2000], 'output': (stderr or '')[:2000], 'expected': (tc.expected or '')[:2000],
            })
            break
        if return_code != 0:
            result_status = 'error'
            error_message = (stderr or stdout or 'Runtime error')[:500]
            failed_count += 1
            details_list.append({
                'test_case_id': tc.id, 'is_sample': tc.is_sample, 'passed': False,
                'input': (tc.input_data or '')[:2000], 'output': (stderr or stdout or '')[:2000], 'expected': (tc.expected or '')[:2000],
            })
            break
        passed = check_output_match(stdout, tc.expected)
        if passed:
            passed_count += 1
        else:
            failed_count += 1
            if result_status == 'passed':
                result_status = 'failed'
                error_message = 'Wrong answer'
        details_list.append({
            'test_case_id': tc.id, 'is_sample': tc.is_sample, 'passed': passed,
            'input': (tc.input_data or '')[:2000], 'output': (stdout or '')[:2000], 'expected': (tc.expected or '')[:2000],
        })
    if result_status == 'passed' and failed_count == 0:
        passed_count = len(cases)
        failed_count = 0
    total_tests = len(cases)
    display_status = 'Accepted' if result_status == 'passed' else 'Wrong Answer'
    if result_status in ('timeout', 'error'):
        display_status = result_status
    attempt_no = CodingSubmission.objects.filter(
        task=task,
        student=request.user,
    ).count() + 1
    sub = CodingSubmission.objects.create(
        organization_id=task.organization_id,
        task=task,
        student=request.user,
        submitted_code=code,
        language='python',
        run_type='SUBMIT',
        total_count=total_tests,
        status=result_status,
        score=int(100 * passed_count / total_tests) if total_tests else 0,
        passed_count=passed_count,
        failed_count=failed_count,
        error_message=error_message,
        runtime_ms=total_time_ms or None,
        attempt_no=attempt_no,
        details_json=details_list,
    )
    if result_status == 'passed':
        CodingProgress.objects.update_or_create(
            student_profile=request.user.student_profile,
            exercise=task,
            defaults={'status': 'completed', 'score': sub.score or 0},
        )
    return Response({
        'status': display_status,
        'passed_tests': passed_count,
        'total_tests': total_tests,
        'submissionId': sub.id,
        'resultStatus': result_status,
        'passedCount': passed_count,
        'totalCases': total_tests,
        'score': sub.score,
        'createdAt': sub.created_at.isoformat(),
    }, status=status.HTTP_201_CREATED)
