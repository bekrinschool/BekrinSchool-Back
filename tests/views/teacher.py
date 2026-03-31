"""
Teacher tests API
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsTeacher
from tests.models import Test, TestResult
from tests.serializers import TestSerializer, TestResultSerializer, TestResultCreateSerializer


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_tests_list_view(request):
    """
    GET /api/teacher/tests - list all tests and results
    POST /api/teacher/tests - create Test (quiz/exam)
    """
    if request.method == 'GET':
        tests = Test.objects.filter(deleted_at__isnull=True).order_by('-created_at')
        results = TestResult.objects.all().select_related('student_profile__user', 'group').order_by('-date')[:100]
        return Response({
            'tests': TestSerializer(tests, many=True).data,
            'results': TestResultSerializer(results, many=True).data,
        })

    if request.method == 'POST':
        serializer = TestSerializer(data=request.data)
        if serializer.is_valid():
            test = serializer.save(created_by=request.user)
            return Response(TestSerializer(test).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_test_result_create_view(request):
    """
    POST /api/teacher/test-results - DISABLED
    Manual grade entry removed. All grades must come from exam grading process.
    """
    return Response(
        {'detail': 'Qiymət əlavə etmə deaktiv edilib. Bütün qiymətlər imtahan yoxlama prosesindən gəlməlidir.'},
        status=status.HTTP_403_FORBIDDEN
    )
