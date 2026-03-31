"""
Global exception handler for consistent API error responses.
Follows DRF convention and returns uniform structure.
"""
import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.conf import settings

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that returns:
    { "detail": str, "code": str (optional), "errors": dict (optional) }
    """
    # STEP 2 — LOG EXCEPTION HANDLER (DRF only, not called for regular Django views)
    request = context.get('request') if context else None
    path = request.path if request else 'unknown'
    is_pdf = '/pdf' in path.lower() if path else False
    
    if is_pdf:
        print(f"[STEP 2] EXCEPTION_HANDLER CALLED for PDF path: {path}")
        print(f"[STEP 2]   Exception type: {type(exc).__name__}")
        print(f"[STEP 2]   Exception: {exc}")
        print(f"[STEP 2]   ⚠️  WARNING: Exception handler replacing response!")
    
    response = exception_handler(exc, context)
    if response is not None:
        if is_pdf:
            print(f"[STEP 2]   Response type: {type(response).__name__}")
            print(f"[STEP 2]   Response Content-Type: {response.get('Content-Type')}")
            print(f"[STEP 2]   Response status: {response.status_code}")
        data = response.data if isinstance(response.data, dict) else {'detail': str(response.data)}
        if 'detail' not in data and response.data:
            data = {'detail': data.get('message', str(response.data))}
        data.setdefault('detail', _get_detail(exc))
        data.setdefault('code', _get_code(exc))
        response.data = data
        return response

    if isinstance(exc, PermissionDenied):
        return Response(
            {'detail': str(exc) or 'Permission denied', 'code': 'permission_denied'},
            status=status.HTTP_403_FORBIDDEN
        )
    if isinstance(exc, DjangoValidationError):
        return Response(
            {'detail': str(exc), 'code': 'validation_error'},
            status=status.HTTP_400_BAD_REQUEST
        )

    logger.exception('Unhandled exception: %s', exc)
    # In DEBUG mode, include more details for troubleshooting
    error_detail = 'An internal error occurred.'
    if settings.DEBUG:
        error_detail = f'An internal error occurred: {str(exc)}'
    # Never expose stack traces to frontend; use standard API error format
    return Response(
        {'detail': error_detail, 'code': 'internal_error'},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )


def _get_detail(exc):
    if hasattr(exc, 'detail'):
        d = exc.detail
        if isinstance(d, list):
            return d[0] if d else 'Error'
        if isinstance(d, dict):
            return d.get('detail', str(d))
        return str(d)
    return str(exc)


def _get_code(exc):
    codes = {
        'AuthenticationFailed': 'invalid_credentials',
        'NotFound': 'not_found',
        'PermissionDenied': 'permission_denied',
        'ValidationError': 'validation_error',
    }
    return codes.get(type(exc).__name__, 'error')
