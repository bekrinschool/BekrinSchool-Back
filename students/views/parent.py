"""
Parent API views. ParentChild links parent User to student User; child profile via student.student_profile.
"""
from calendar import monthrange
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsParent
from django.db.models import Count
from datetime import date, timedelta
from students.models import ParentChild, StudentProfile
from attendance.models import AttendanceRecord
from attendance.serializers import AttendanceRecordSerializer
from payments.models import Payment
from payments.serializers import PaymentSerializer
from coding.models import CodingTask, CodingSubmission


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsParent])
def parent_children_view(request):
    """
    GET /api/parent/children
    Get parent's children (via ParentChild.student -> StudentProfile) with stats.
    """
    parent_children = ParentChild.objects.filter(
        parent=request.user,
        student__is_active=True,
        student__student_profile__is_deleted=False,
    ).select_related('student', 'student__student_profile')
    
    result = []
    for pc in parent_children:
        student_user = pc.student
        try:
            child = student_user.student_profile
        except StudentProfile.DoesNotExist:
            continue
        
        thirty_days_ago = date.today() - timedelta(days=30)
        attendance_records = AttendanceRecord.objects.filter(
            student_profile=child,
            lesson_date__gte=thirty_days_ago
        )
        total_days = attendance_records.count()
        present_days = attendance_records.filter(status='present').count()
        attendance_percent = int((present_days / total_days * 100)) if total_days > 0 else 0
        
        # Son Test: last published exam result (sync with İmtahanlar page; no legacy TestResult)
        last_test = None
        from tests.models import ExamAttempt
        from django.db.models import Q
        last_attempt = (
            ExamAttempt.objects.filter(
                student=student_user,
                finished_at__isnull=False,
                is_result_published=True,
                is_archived=False,
                exam__is_deleted=False,
                is_result_session_deleted=False,
            )
            .exclude(exam__status='deleted')
            .filter(Q(exam_run__isnull=True) | Q(exam_run__is_history_deleted=False))
            .select_related('exam')
            .order_by('-finished_at')
            .first()
        )
        if last_attempt and last_attempt.exam:
            score = float(last_attempt.total_score if last_attempt.total_score is not None else last_attempt.manual_score or last_attempt.auto_score or 0)
            max_score = float(last_attempt.exam.max_score or (100 if last_attempt.exam.type == 'quiz' else 150))
            last_test = {
                'name': last_attempt.exam.title,
                'score': score,
                'maxScore': max_score,
                'date': last_attempt.finished_at.isoformat() if last_attempt.finished_at else None,
            }
        
        total_tasks = CodingTask.objects.filter(
            deleted_at__isnull=True,
            is_active=True,
        ).count() or 1
        solved_count = CodingSubmission.objects.filter(
            student_id=student_user.id,
            status='passed',
            task__deleted_at__isnull=True,
        ).values('task_id').distinct().count()
        coding_percent = int((solved_count / total_tasks * 100)) if total_tasks > 0 else 0
        last_submission = CodingSubmission.objects.filter(
            student_id=student_user.id,
        ).order_by('-created_at').values_list('created_at', flat=True).first()

        result.append({
            'id': child.id,
            'email': child.user.email,
            'fullName': child.user.full_name,
            'class': child.grade,
            'attendancePercent': attendance_percent,
            'balance': float(child.balance),
            'lastTest': last_test,
            'codingSolvedCount': solved_count,
            'codingTotalTasks': total_tasks,
            'codingPercent': coding_percent,
            'codingLastActivity': last_submission.isoformat() if last_submission else None,
        })
    
    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsParent])
def parent_attendance_view(request):
    """
    GET /api/parent/attendance?studentId=
    studentId = StudentProfile.id (child's profile id).
    """
    student_id = request.query_params.get('studentId')
    if not student_id:
        return Response({'detail': 'studentId is required'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        ParentChild.objects.get(
            parent=request.user,
            student__student_profile__id=student_id,
            student__is_active=True,
            student__student_profile__is_deleted=False,
        )
    except ParentChild.DoesNotExist:
        return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
    
    attendance = AttendanceRecord.objects.filter(
        student_profile_id=student_id
    ).select_related('group').order_by('-lesson_date')
    
    serializer = AttendanceRecordSerializer(attendance, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsParent])
def parent_attendance_monthly_view(request):
    """
    GET /api/parent/attendance/monthly?studentId=&month=&year=
    Returns monthly stats for parent's child: Present, Absent, Late, Excused, Attendance %
    """
    student_id = request.query_params.get('studentId')
    month = request.query_params.get('month', str(date.today().month))
    year = request.query_params.get('year', str(date.today().year))

    if not student_id:
        return Response(
            {'detail': 'studentId is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        month = int(month)
        year = int(year)
    except ValueError:
        return Response(
            {'detail': 'Invalid month or year'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        ParentChild.objects.get(
            parent=request.user,
            student__student_profile__id=student_id,
            student__is_active=True,
            student__student_profile__is_deleted=False,
        )
    except ParentChild.DoesNotExist:
        return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    records = (
        AttendanceRecord.objects.filter(
            student_profile_id=student_id,
            lesson_date__gte=start_date,
            lesson_date__lte=end_date,
        )
        .values('status')
        .annotate(cnt=Count('id'))
    )
    stats = {'present': 0, 'absent': 0, 'late': 0, 'excused': 0}
    for r in records:
        if r['status'] in stats:
            stats[r['status']] = r['cnt']

    total = stats['present'] + stats['absent'] + stats['late'] + stats['excused']
    pct = round((stats['present'] / total * 100), 1) if total > 0 else 0

    return Response({
        'year': year,
        'month': month,
        'studentId': student_id,
        'present': stats['present'],
        'absent': stats['absent'],
        'late': stats['late'],
        'excused': stats['excused'],
        'attendancePercent': pct,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsParent])
def parent_payments_view(request):
    """
    GET /api/parent/payments?studentId=
    studentId = StudentProfile.id.
    """
    student_id = request.query_params.get('studentId')
    if not student_id:
        return Response({'detail': 'studentId is required'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        ParentChild.objects.get(
            parent=request.user,
            student__student_profile__id=student_id,
            student__is_active=True,
            student__student_profile__is_deleted=False,
        )
    except ParentChild.DoesNotExist:
        return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
    
    payments = Payment.objects.filter(
        student_profile_id=student_id,
        deleted_at__isnull=True
    ).select_related('group').order_by('-payment_date')
    
    serializer = PaymentSerializer(payments, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsParent])
def parent_test_results_view(request):
    """
    GET /api/parent/test-results?studentId=
    studentId = StudentProfile.id
    """
    from tests.models import TestResult
    from tests.serializers import TestResultSerializer

    student_id = request.query_params.get('studentId')
    if not student_id:
        return Response({'detail': 'studentId is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        ParentChild.objects.get(
            parent=request.user,
            student__student_profile__id=student_id,
            student__is_active=True,
            student__student_profile__is_deleted=False,
        )
    except ParentChild.DoesNotExist:
        return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

    results = TestResult.objects.filter(
        student_profile_id=student_id
    ).select_related('group').order_by('-date')

    serializer = TestResultSerializer(results, many=True)
    return Response(serializer.data)


def _parent_canvas_list(attempt, request):
    """Build canvas list for attempt (same shape as student result)."""
    from tests.models import ExamAttemptCanvas
    canvases = ExamAttemptCanvas.objects.filter(attempt=attempt).order_by('situation_index', 'question_id')
    out = []
    for c in canvases:
        rec = {
            'canvasId': c.id,
            'questionId': c.question_id,
            'situationIndex': c.situation_index,
            'updatedAt': c.updated_at.isoformat() if c.updated_at else None,
        }
        if c.image:
            rec['imageUrl'] = request.build_absolute_uri(c.image.url) if request else c.image.url
        else:
            rec['imageUrl'] = None
        out.append(rec)
    return out


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsParent])
def parent_exam_attempt_detail_view(request, exam_id, attempt_id):
    """
    GET /api/parent/exams/<exam_id>/attempts/<attempt_id>/detail?studentId=
    Returns published attempt detail with questions (yourAnswer vs correctAnswer, points) and canvases (same as student).
    """
    from tests.models import ExamAttempt
    from tests.views.exams import _build_published_result_questions_and_canvases
    student_id = request.query_params.get('studentId')
    if not student_id:
        return Response({'detail': 'studentId required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        pc = ParentChild.objects.get(
            parent=request.user,
            student__student_profile__id=student_id,
            student__is_active=True,
            student__student_profile__is_deleted=False,
        )
    except ParentChild.DoesNotExist:
        return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
    try:
        attempt = ExamAttempt.objects.select_related('exam', 'student', 'exam_run').prefetch_related(
            'answers__question', 'answers__question__options'
        ).get(
            pk=attempt_id,
            exam_id=exam_id,
            student=pc.student,
            finished_at__isnull=False,
        )
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.is_result_session_deleted:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.exam_run_id and getattr(attempt.exam_run, 'is_history_deleted', False):
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if getattr(attempt.exam, 'is_deleted', False) or getattr(attempt.exam, 'status', None) == 'deleted':
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not attempt.is_result_published:
        return Response({'detail': 'Results not yet published'}, status=status.HTTP_403_FORBIDDEN)
    from decimal import Decimal
    manual = attempt.manual_score
    auto = attempt.auto_score
    total = float(attempt.total_score) if attempt.total_score is not None else (float(auto or 0) + float(manual or 0))
    max_s = float(attempt.exam.max_score or (100 if attempt.exam.type == 'quiz' else 150))
    score = max(0.0, min(total, max_s))
    content_lockdown = bool(attempt.exam.is_result_published or attempt.is_result_published)
    breakdown = _build_published_result_questions_and_canvases(attempt, request, content_lockdown=content_lockdown)
    points_from_correct = Decimal('0')
    penalty_from_wrong = Decimal('0')
    situation_score = Decimal('0')
    for a in attempt.answers.all():
        if a.requires_manual_check and a.manual_score is not None:
            situation_score += a.manual_score
        else:
            asc = a.auto_score or Decimal('0')
            if asc > 0:
                points_from_correct += asc
            elif asc < 0:
                penalty_from_wrong += asc
    resp = {
        'attemptId': attempt.id,
        'examId': attempt.exam_id,
        'title': attempt.exam.title,
        'status': 'published',
        'autoScore': float(attempt.auto_score or 0),
        'manualScore': float(manual) if manual is not None else None,
        'totalScore': score,
        'score': score,
        'maxScore': max_s,
        'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
        'questions': breakdown['questions'],
        'canvases': breakdown['canvases'],
        'scoreBreakdown': {
            'pointsFromCorrect': round(float(points_from_correct), 2),
            'penaltyFromWrong': round(float(penalty_from_wrong), 2),
            'situationScore': round(float(situation_score), 2),
            'total': round(score, 2),
        },
    }
    if content_lockdown:
        resp['contentLocked'] = True
    has_pdf = attempt.exam_run and attempt.exam.source_type in ('PDF', 'JSON') and (
        (attempt.exam.pdf_document and getattr(attempt.exam.pdf_document, 'file', None)) or attempt.exam.pdf_file
    )
    if has_pdf:
        from tests.pdf_auth import generate_pdf_access_token
        from tests.models import PdfScribble
        from tests.views.exams import _get_run_page_urls
        token = generate_pdf_access_token(attempt.student.id, attempt.exam_run.id)
        resp['pdfUrl'] = request.build_absolute_uri(f'/api/student/runs/{attempt.exam_run.id}/pages')
        resp['pdfScribbles'] = [{'pageIndex': s.page_index, 'drawingData': s.drawing_data or {}} for s in PdfScribble.objects.filter(attempt=attempt).order_by('page_index')]
        resp['pages'] = _get_run_page_urls(attempt.exam_run.id, request)
    return Response(resp)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsParent])
def parent_exam_results_view(request):
    """
    GET /api/parent/exam-results?studentId=
    studentId = StudentProfile.id. Returns child's exam attempts (submitted + published).
    Mask score when not published; show status.
    """
    from tests.models import ExamAttempt

    student_id = request.query_params.get('studentId')
    if not student_id:
        return Response({'detail': 'studentId is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        pc = ParentChild.objects.get(
            parent=request.user,
            student__student_profile__id=student_id,
            student__is_active=True,
            student__student_profile__is_deleted=False,
        )
    except ParentChild.DoesNotExist:
        return Response({'detail': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
    student_user = pc.student
    from django.db.models import Q
    attempts = ExamAttempt.objects.filter(
        student=student_user,
        finished_at__isnull=False,
        is_archived=False,
        exam__is_deleted=False,
        is_result_session_deleted=False,
    ).exclude(exam__status='deleted').filter(
        Q(exam_run__isnull=True) | Q(exam_run__is_history_deleted=False),
    ).select_related('exam').order_by('-finished_at')
    data = []
    for a in attempts:
        is_published = a.is_result_published
        max_score = float(a.exam.max_score or (100 if a.exam.type == 'quiz' else 150))
        data.append({
            'attemptId': a.id,
            'examId': a.exam_id,
            'title': a.exam.title,
            'examType': a.exam.type or 'exam',
            'status': 'PUBLISHED' if is_published else ('WAITING_MANUAL' if a.is_checked else 'SUBMITTED'),
            'is_result_published': is_published,
            'score': float(a.manual_score if a.manual_score is not None else a.auto_score or 0) if is_published else None,
            'maxScore': max_score,
            'finishedAt': a.finished_at.isoformat() if a.finished_at else None,
        })
    return Response(data)
