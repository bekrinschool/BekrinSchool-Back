"""
URL configuration for bekrin-back project
"""
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)


@require_http_methods(["GET"])
def health_view(request):
    """Minimal health check for connectivity verification. No auth required."""
    return JsonResponse({'status': 'ok', 'service': 'bekrin-back'})


@require_http_methods(["GET"])
def system_health_view(request):
    """
    Full system health check for monitoring.
    Returns db, auth, coding, exams status. No auth required.
    """
    result = {'db': 'ok', 'auth': 'ok', 'coding': 'ok', 'exams': 'ok'}
    try:
        from django.db import connection
        connection.ensure_connection()
    except Exception as e:
        result['db'] = f'error: {str(e)[:80]}'
    try:
        from django.contrib.auth import get_user_model
        get_user_model().objects.exists()
    except Exception as e:
        result['auth'] = f'error: {str(e)[:80]}'
    try:
        from coding.models import CodingTask
        CodingTask.objects.exists()
    except Exception as e:
        result['coding'] = f'error: {str(e)[:80]}'
    try:
        from tests.models import Exam
        Exam.objects.exists()
    except Exception as e:
        result['exams'] = f'error: {str(e)[:80]}'
    return JsonResponse(result)


@require_http_methods(["GET"])
def api_root(request):
    """Root endpoint - API information"""
    return JsonResponse({
        'name': 'Bekrin School API',
        'version': '1.0.0',
        'description': 'DIM imtahanına hazırlıq üçün kurs idarəetmə sistemi API',
        'endpoints': {
            'health': '/api/health/',
            'auth': '/api/auth/',
            'teacher': '/api/teacher/',
            'student': '/api/student/',
            'parent': '/api/parent/',
            'docs': '/api/docs/',
            'schema': '/api/schema/',
        }
    })


urlpatterns = [
    path('', api_root, name='api-root'),
    path('admin', RedirectView.as_view(url='/admin/', permanent=False)),
    path('admin/', admin.site.urls),
    path('api', RedirectView.as_view(url='/api/', permanent=False)),
    path('api/', api_root),
    path('api/health/', health_view, name='api-health'),
    path('api/system/health/', system_health_view, name='api-system-health'),
    
    # API Schema
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    
    # API endpoints
    path('api/auth/', include('accounts.urls')),
    path('api/users/', include('accounts.urls_users')),
    path('api/teacher/notifications/', include('notifications.urls')),  # Must come before general teacher URLs
    path('api/teacher/', include('groups.urls.teacher')),
    path('api/student/', include('students.urls.student')),
    path('api/parent/', include('students.urls.parent')),
]

# Serve media files (PDFs, uploads) with iframe exemption so PDFs display in teacher/student dashboards.
@xframe_options_exempt
def media_serve(request, path):
    """Serve media files; allow iframe embedding for PDF preview."""
    return serve(request, path, document_root=settings.MEDIA_ROOT)

media_url_pattern = settings.MEDIA_URL.lstrip('/')
urlpatterns += [path(f'{media_url_pattern}<path:path>', media_serve)]
