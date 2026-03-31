"""
Serializers for students app
"""
from rest_framework import serializers
from .models import StudentProfile, ParentChild
from accounts.serializers import UserSerializer
from .utils import get_teacher_display_balance


class StudentProfileSerializer(serializers.ModelSerializer):
    """Student Profile serializer. status from deleted_at; phone from user. userId for exam assignment."""
    email = serializers.EmailField(source='user.email', read_only=True)
    fullName = serializers.CharField(source='user.full_name', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True, allow_null=True)
    status = serializers.SerializerMethodField()
    userId = serializers.IntegerField(source='user_id', read_only=True)
    
    class Meta:
        model = StudentProfile
        fields = ['id', 'userId', 'email', 'fullName', 'grade', 'phone', 'balance', 'status']
        read_only_fields = ['id']
    
    def get_status(self, obj):
        return 'deleted' if obj.deleted_at else 'active'
    
    def to_representation(self, instance):
        """Convert grade to 'class' and balance to float in response.
        Add displayBalanceTeacher for teacher views."""
        data = super().to_representation(instance)
        if 'grade' in data:
            data['class'] = data.pop('grade')
        # Convert DecimalField balance to float for frontend
        if 'balance' in data and data['balance'] is not None:
            real_balance = float(data['balance'])
            data['balance'] = real_balance
            # Add teacher display balance (real / 4)
            data['displayBalanceTeacher'] = get_teacher_display_balance(instance.balance)
        return data


class StudentProfileUpdateSerializer(serializers.ModelSerializer):
    """Student Profile update serializer. fullName/phone update User; grade/balance update profile.
    Accepts 'class' (frontend) as alias for grade."""
    fullName = serializers.CharField(source='user.full_name', required=False)
    phone = serializers.CharField(source='user.phone', required=False, allow_null=True, allow_blank=True)
    
    class Meta:
        model = StudentProfile
        fields = ['fullName', 'grade', 'phone', 'balance']
    
    def validate_balance(self, value):
        # Allow negative (e.g. after lesson debits or manual correction)
        return value
    
    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        if user_data:
            user = instance.user
            if 'full_name' in user_data:
                user.full_name = user_data['full_name']
            if 'phone' in user_data:
                user.phone = user_data['phone'] or None
            user.save()
        return super().update(instance, validated_data)


class ParentChildSerializer(serializers.ModelSerializer):
    """Parent-Child relationship. Exposes child profile via student.student_profile."""
    child = serializers.SerializerMethodField()
    
    class Meta:
        model = ParentChild
        fields = ['id', 'parent', 'student', 'child', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def get_child(self, obj):
        try:
            return StudentProfileSerializer(obj.student.student_profile).data
        except StudentProfile.DoesNotExist:
            return None
