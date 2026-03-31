"""
Serializers for accounts app
"""
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """User serializer for API responses"""
    fullName = serializers.CharField(source='full_name', read_only=True)
    mustChangePassword = serializers.BooleanField(source='must_change_password', read_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'email', 'fullName', 'role', 'mustChangePassword']
        read_only_fields = ['id', 'email', 'role', 'mustChangePassword']


class LoginSerializer(serializers.Serializer):
    """Login serializer"""
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    
    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')
        
        if not email or not password:
            raise serializers.ValidationError('Must include "email" and "password".')
        
        # Custom User model uses email as USERNAME_FIELD
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Use AuthenticationFailed for 401 status code
            raise AuthenticationFailed('Invalid email or password.')
        
        # Check password
        if not user.check_password(password):
            # Use AuthenticationFailed for 401 status code
            raise AuthenticationFailed('Invalid email or password.')
        
        # Check if user is active
        if not user.is_active:
            raise AuthenticationFailed('User account is disabled.')
        
        attrs['user'] = user
        return attrs


class LoginResponseSerializer(serializers.Serializer):
    """Login response serializer"""
    accessToken = serializers.CharField()
    user = UserSerializer()
