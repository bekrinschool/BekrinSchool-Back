"""
Serializers for notifications.
"""
from rest_framework import serializers
from notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    studentId = serializers.CharField(source='student.id', read_only=True)
    studentName = serializers.CharField(source='student.user.full_name', read_only=True)
    groupId = serializers.CharField(source='group.id', read_only=True, allow_null=True)
    groupName = serializers.CharField(source='group.name', read_only=True, allow_null=True)
    
    class Meta:
        model = Notification
        fields = [
            'id',
            'type',
            'studentId',
            'studentName',
            'groupId',
            'groupName',
            'message',
            'is_read',
            'is_resolved',
            'created_at',
            'resolved_at',
        ]
        read_only_fields = ['id', 'created_at', 'resolved_at']
