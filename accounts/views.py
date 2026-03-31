"""
Authentication views
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import LoginSerializer, LoginResponseSerializer, UserSerializer


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """
    POST /api/auth/login
    Login with email and password
    Returns: {accessToken, user: {email, fullName, role}}
    
    Status codes:
    - 200: Success
    - 400: Invalid request format (missing fields, invalid email format)
    - 401: Invalid credentials or disabled account
    - 500: Unexpected server error
    """
    serializer = LoginSerializer(data=request.data)
    
    try:
        if not serializer.is_valid():
            # Return 400 for validation errors (missing fields, invalid format)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        user = serializer.validated_data['user']
        refresh = RefreshToken.for_user(user)
        
        # Response format matching frontend expectations (role lowercase for consistent routing)
        response_data = {
            'accessToken': str(refresh.access_token),
            'user': {
                'email': user.email,
                'fullName': user.full_name,
                'role': (user.role or 'student').lower(),
                'mustChangePassword': getattr(user, 'must_change_password', False),
            }
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
    
    except AuthenticationFailed as e:
        # AuthenticationFailed exceptions are raised by serializer for invalid credentials
        # DRF will automatically return 401 status code
        return Response(
            {'detail': str(e.detail) if hasattr(e, 'detail') else str(e)},
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    except Exception as e:
        # Log error for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Unexpected login error: {str(e)}', exc_info=True)
        
        # Return 500 only for unexpected server errors
        return Response(
            {'detail': 'An error occurred during login. Please try again.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    """
    POST /api/auth/logout
    Logout user
    Note: Token blacklist requires 'rest_framework_simplejwt.token_blacklist' app
    For now, logout just returns success (client should clear token)
    """
    # TODO: Add token blacklist app if needed for production
    # try:
    #     refresh_token = request.data.get('refresh_token')
    #     if refresh_token:
    #         token = RefreshToken(refresh_token)
    #         token.blacklist()
    # except Exception:
    #     pass
    
    return Response({'detail': 'Successfully logged out.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    """
    GET /api/auth/me
    Get current user info
    Returns: {email, fullName, role, mustChangePassword}
    """
    serializer = UserSerializer(request.user)
    data = serializer.data
    impersonator_id = request.session.get('impersonator_id')
    data['is_impersonating'] = bool(impersonator_id)
    data['impersonator_id'] = impersonator_id
    return Response(data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    """
    POST /api/auth/change-password
    Body: { currentPassword, newPassword }
    Change user password. Clears must_change_password on success.
    """
    current = request.data.get('currentPassword') or request.data.get('current_password')
    new_pw = request.data.get('newPassword') or request.data.get('new_password')

    if not current or not new_pw:
        return Response(
            {'detail': 'currentPassword and newPassword are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if len(new_pw) < 8:
        return Response(
            {'detail': 'New password must be at least 8 characters'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = request.user
    if not user.check_password(current):
        return Response(
            {'detail': 'Current password is incorrect'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.set_password(new_pw)
    user.must_change_password = False
    user.save(update_fields=['password', 'must_change_password'])
    return Response({'detail': 'Password changed successfully'}, status=status.HTTP_200_OK)
