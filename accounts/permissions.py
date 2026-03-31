"""
Custom permissions for role-based access
"""
from rest_framework import permissions


class IsTeacher(permissions.BasePermission):
    """Permission check for teacher role"""
    
    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            request.user.role == 'teacher'
        )


class IsStudent(permissions.BasePermission):
    """Permission check for student role"""
    
    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            request.user.role == 'student'
        )


class IsParent(permissions.BasePermission):
    """Permission check for parent role"""
    
    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            request.user.role == 'parent'
        )


class IsStudentOrSignedToken(permissions.BasePermission):
    """
    Permission that allows either:
    1. Authenticated student via JWT (normal API access)
    2. Valid signed token via query parameter (iframe PDF access)
    
    Sets request.user from token if valid, otherwise uses existing auth.
    """
    
    def has_permission(self, request, view):
        # Check if user is authenticated via JWT (normal case)
        if request.user and request.user.is_authenticated:
            # Verify student role
            if request.user.role == 'student':
                return True
        
        # Check for signed token in query parameters (iframe case)
        token = request.query_params.get('token')
        if token:
            # Get run_id from view kwargs
            run_id = view.kwargs.get('run_id')
            if not run_id:
                return False
            
            # Validate token
            from tests.pdf_auth import validate_pdf_access_token
            is_valid, user_id = validate_pdf_access_token(token, run_id)
            
            if is_valid and user_id:
                # Load user from token and set on request
                from accounts.models import User
                try:
                    user = User.objects.get(pk=user_id)
                    if user.role == 'student':
                        # Set user on request for view logic
                        request.user = user
                        request._pdf_auth_via_token = True  # Flag for logging
                        return True
                except User.DoesNotExist:
                    return False
        
        return False
