"""Coding archive API: list archived topics/tasks, restore, hard-delete."""
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsTeacher
from coding.models import CodingTopic, CodingTask
from coding.serializers import CodingTopicSerializer, CodingTaskSerializer


def _paginate(items, request, page_size=20):
    page = int(request.query_params.get('page', 1))
    page_size = min(int(request.query_params.get('page_size', page_size)), 100)
    offset = (page - 1) * page_size
    sliced = items[offset:offset + page_size + 1]
    has_next = len(sliced) > page_size
    if has_next:
        sliced = sliced[:page_size]
    return sliced, {'page': page, 'page_size': page_size, 'has_next': has_next}


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_coding_topics_view(request):
    q = request.query_params.get('q', '').strip()
    qs = CodingTopic.objects.filter(is_archived=True).order_by('name')
    if q:
        qs = qs.filter(name__icontains=q)
    items = list(qs)
    items, meta = _paginate(items, request)
    return Response({'items': CodingTopicSerializer(items, many=True).data, 'meta': meta})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def archive_coding_tasks_view(request):
    q = request.query_params.get('q', '').strip()
    qs = CodingTask.objects.filter(is_archived=True).select_related('topic').order_by('-created_at')
    if not getattr(settings, 'SINGLE_TENANT', True):
        qs = qs.filter(created_by=request.user)
    if q:
        qs = qs.filter(title__icontains=q)
    items = list(qs)
    items, meta = _paginate(items, request)
    return Response({'items': CodingTaskSerializer(items, many=True).data, 'meta': meta})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_coding_topic_view(request, pk):
    try:
        topic = CodingTopic.objects.get(pk=pk)
    except CodingTopic.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not topic.is_archived:
        return Response({'detail': 'Already active'}, status=status.HTTP_400_BAD_REQUEST)
    topic.is_archived = False
    topic.save(update_fields=['is_archived'])
    return Response({'id': topic.pk, 'message': 'Restored'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def restore_coding_task_view(request, pk):
    try:
        qs = CodingTask.objects.filter(pk=pk)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(created_by=request.user)
        task = qs.get()
    except CodingTask.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not task.is_archived:
        return Response({'detail': 'Already active'}, status=status.HTTP_400_BAD_REQUEST)
    task.is_archived = False
    task.save(update_fields=['is_archived'])
    return Response({'id': task.pk, 'message': 'Restored'})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def hard_delete_coding_topic_view(request, pk):
    try:
        topic = CodingTopic.objects.get(pk=pk)
    except CodingTopic.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not topic.is_archived:
        return Response({'detail': 'Archive first'}, status=status.HTTP_400_BAD_REQUEST)
    topic.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def hard_delete_coding_task_view(request, pk):
    try:
        qs = CodingTask.objects.filter(pk=pk)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(created_by=request.user)
        task = qs.get()
    except CodingTask.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if not task.is_archived:
        return Response({'detail': 'Archive first'}, status=status.HTTP_400_BAD_REQUEST)
    task.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
