"""
Archive API: list archived items, restore, hard-delete (2-step confirmation).
"""
from django.conf import settings
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsTeacher

from tests.models import (
    QuestionTopic,
    Question,
    Exam,
    TeacherPDF,
)
from tests.serializers import (
    QuestionTopicSerializer,
    QuestionSerializer,
    ExamSerializer,
    TeacherPDFSerializer,
)


def _paginate(qs, request, page_size=20):
    page = int(request.query_params.get('page', 1))
    page_size = min(int(request.query_params.get('page_size', page_size)), 100)
    offset = (page - 1) * page_size
    items = qs[offset:offset + page_size + 1]
    has_next = len(items) > page_size
    if has_next:
        items = items[:page_size]
    return items, {'page': page, 'page_size': page_size, 'has_next': has_next}


# ---------- Archive lists (is_archived=True) ----------

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_question_topics_view(request):
    q = request.query_params.get('q', '').strip()
    qs = QuestionTopic.objects.filter(is_archived=True).order_by('order', 'name')
    if q:
        qs = qs.filter(name__icontains=q)
    items, meta = _paginate(list(qs), request)
    return Response({
        'items': QuestionTopicSerializer(items, many=True).data,
        'meta': meta,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_questions_view(request):
    q = request.query_params.get('q', '').strip()
    qs = Question.objects.filter(is_archived=True).select_related('topic').prefetch_related('options').order_by('-created_at')
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(short_title__icontains=q))
    items, meta = _paginate(list(qs), request)
    return Response({
        'items': QuestionSerializer(items, many=True).data,
        'meta': meta,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_exams_view(request):
    from django.db.models import Count
    q = request.query_params.get('q', '').strip()
    qs = Exam.objects.filter(is_archived=True).select_related('created_by').prefetch_related('assignments__group').annotate(
        attempt_count=Count('attempts', distinct=True)
    ).order_by('-created_at')
    if not getattr(settings, 'SINGLE_TENANT', True):
        qs = qs.filter(created_by=request.user)
    if q:
        qs = qs.filter(title__icontains=q)
    items = list(qs)
    page = int(request.query_params.get('page', 1))
    page_size = min(int(request.query_params.get('page_size', 20)), 100)
    offset = (page - 1) * page_size
    paginated = items[offset:offset + page_size + 1]
    has_next = len(paginated) > page_size
    if has_next:
        paginated = paginated[:page_size]
    data = []
    for e in paginated:
        d = ExamSerializer(e).data
        d['attemptCount'] = getattr(e, 'attempt_count', e.attempts.count())
        data.append(d)
    return Response({
        'items': data,
        'meta': {'page': page, 'page_size': page_size, 'has_next': has_next},
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_pdfs_view(request):
    q = request.query_params.get('q', '').strip()
    qs = TeacherPDF.objects.filter(is_archived=True).order_by('-created_at')
    if not getattr(settings, 'SINGLE_TENANT', True):
        qs = qs.filter(teacher=request.user)
    if q:
        qs = qs.filter(title__icontains=q)
    items, meta = _paginate(list(qs), request)
    return Response({
        'items': TeacherPDFSerializer(items, many=True, context={'request': request}).data,
        'meta': meta,
    })


# ---------- Restore ----------

def _restore_item(model_cls, pk, request, created_by_filter=None):
    try:
        qs = model_cls.objects.filter(pk=pk)
        if created_by_filter:
            qs = qs.filter(**created_by_filter)
        obj = qs.get()
    except model_cls.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not obj.is_archived:
        return Response({'detail': 'Already active'}, status=status.HTTP_400_BAD_REQUEST)
    obj.is_archived = False
    if hasattr(obj, 'is_deleted'):
        obj.is_deleted = False
    save_fields = ['is_archived']
    if hasattr(obj, 'is_deleted'):
        save_fields.append('is_deleted')
    obj.save(update_fields=save_fields)
    return Response({'id': obj.pk, 'message': 'Restored'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_question_topic_view(request, pk):
    return _restore_item(QuestionTopic, pk, request)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_question_view(request, pk):
    return _restore_item(Question, pk, request)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_exam_view(request, pk):
    try:
        exam = Exam.objects.get(pk=pk, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not exam.is_archived:
        return Response({'detail': 'Already active'}, status=status.HTTP_400_BAD_REQUEST)
    exam.is_archived = False
    exam.archived_at = None
    exam.is_deleted = False
    exam.deleted_at = None
    if exam.status == 'deleted':
        exam.status = 'draft'
    exam.save(update_fields=['is_archived', 'archived_at', 'is_deleted', 'deleted_at', 'status'])
    return Response({'id': exam.pk, 'message': 'Restored'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_pdf_view(request, pk):
    try:
        qs = TeacherPDF.objects.filter(pk=pk)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(teacher=request.user)
        pdf = qs.get()
    except TeacherPDF.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not pdf.is_archived:
        return Response({'detail': 'Already active'}, status=status.HTTP_400_BAD_REQUEST)
    pdf.is_archived = False
    pdf.is_deleted = False
    pdf.save(update_fields=['is_archived', 'is_deleted'])
    return Response({'id': pdf.pk, 'message': 'Restored'})


# ---------- Hard delete (only when is_archived=True, 2-step confirmation done in frontend) ----------

@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def hard_delete_question_topic_view(request, pk):
    """Direct permanent delete. Cascade deletes all questions (and their options) in this topic."""
    try:
        obj = QuestionTopic.objects.get(pk=pk)
    except QuestionTopic.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    obj.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def hard_delete_question_view(request, pk):
    """Direct permanent delete. Cascade deletes all options for this question."""
    try:
        obj = Question.objects.get(pk=pk)
    except Question.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    obj.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def hard_delete_exam_view(request, pk):
    """Hard-delete an archived exam. Cascade deletes all attempts, answers, runs, assignments, etc."""
    try:
        exam = Exam.objects.get(pk=pk, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not exam.is_archived:
        return Response({'detail': 'Archive first'}, status=status.HTTP_400_BAD_REQUEST)
    # Cascade delete: Django CASCADE on Exam removes runs, attempts, answers, canvases,
    # assignments, student_assignments, exam_questions, and GradingAuditLog (via attempt).
    exam.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def hard_delete_pdf_view(request, pk):
    try:
        qs = TeacherPDF.objects.filter(pk=pk)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(teacher=request.user)
        pdf = qs.get()
    except TeacherPDF.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not pdf.is_archived:
        return Response({'detail': 'Archive first'}, status=status.HTTP_400_BAD_REQUEST)
    pdf.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def bulk_delete_exams_view(request):
    """Bulk delete archived exams. Body: { ids: [1, 2, 3] }"""
    ids = request.data.get('ids', [])
    if not isinstance(ids, list) or not ids:
        return Response({'detail': 'ids array required'}, status=status.HTTP_400_BAD_REQUEST)
    
    qs = Exam.objects.filter(pk__in=ids, is_archived=True)
    if not getattr(settings, 'SINGLE_TENANT', True):
        qs = qs.filter(created_by=request.user)
    
    # Check for attempts
    exams_with_attempts = qs.filter(attempts__isnull=False).distinct()
    if exams_with_attempts.exists():
        return Response({
            'detail': 'Some exams have attempts and cannot be deleted',
            'exam_ids': list(exams_with_attempts.values_list('id', flat=True))
        }, status=status.HTTP_400_BAD_REQUEST)
    
    deleted_count = qs.count()
    qs.delete()
    return Response({'deleted': deleted_count, 'message': f'{deleted_count} exam(s) deleted'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def bulk_delete_questions_view(request):
    """Bulk delete archived questions. Body: { ids: [1, 2, 3] }"""
    ids = request.data.get('ids', [])
    if not isinstance(ids, list) or not ids:
        return Response({'detail': 'ids array required'}, status=status.HTTP_400_BAD_REQUEST)
    
    qs = Question.objects.filter(pk__in=ids, is_archived=True)
    deleted_count = qs.count()
    qs.delete()
    return Response({'deleted': deleted_count, 'message': f'{deleted_count} question(s) deleted'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def bulk_delete_pdfs_view(request):
    """Bulk delete archived PDFs. Body: { ids: [1, 2, 3] }"""
    ids = request.data.get('ids', [])
    if not isinstance(ids, list) or not ids:
        return Response({'detail': 'ids array required'}, status=status.HTTP_400_BAD_REQUEST)
    
    qs = TeacherPDF.objects.filter(pk__in=ids, is_archived=True)
    if not getattr(settings, 'SINGLE_TENANT', True):
        qs = qs.filter(teacher=request.user)
    
    deleted_count = qs.count()
    qs.delete()
    return Response({'deleted': deleted_count, 'message': f'{deleted_count} PDF(s) deleted'})
