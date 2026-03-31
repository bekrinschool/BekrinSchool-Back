"""
Serializers for groups app
"""
from rest_framework import serializers
from .models import Group, GroupStudent, derive_display_name_from_days
from students.serializers import StudentProfileSerializer


class GroupSerializer(serializers.ModelSerializer):
    """Group serializer. lesson_days alias for days_of_week; order alias for sort_order (frontend compat)."""
    studentCount = serializers.IntegerField(source='student_count', read_only=True)
    active = serializers.BooleanField(source='is_active', read_only=True)
    order = serializers.IntegerField(source='sort_order', read_only=True)
    lesson_days = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=7),
        source='days_of_week',
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = Group
        fields = [
            'id', 'name', 'display_name', 'display_name_is_manual',
            'lesson_days', 'start_time', 'active', 'order', 'sort_order', 'studentCount',
            'monthly_fee', 'monthly_lessons_count',
        ]
        read_only_fields = ['id']

    def validate_lesson_days(self, value):
        if value is not None and len(value) == 0:
            raise serializers.ValidationError("Ən azı bir dərs günü seçilməlidir.")
        if value is not None:
            valid = [int(x) for x in value if isinstance(x, (int, str)) and 1 <= int(x) <= 7]
            return sorted(set(valid))
        return value

    def validate(self, attrs):
        # lesson_days is source='days_of_week', so after field validation it's in attrs as days_of_week
        lesson_days = attrs.get('days_of_week')
        display_name_is_manual = attrs.get('display_name_is_manual')
        display_name = attrs.get('display_name')
        instance = self.instance

        if lesson_days is None and instance:
            lesson_days = instance.days_of_week or []

        if lesson_days is not None and len(lesson_days) == 0:
            raise serializers.ValidationError({'lesson_days': 'Ən azı bir dərs günü seçilməlidir.'})

        if not display_name_is_manual and lesson_days:
            start_time = attrs.get('start_time') or (instance.start_time if instance else None)
            attrs['display_name'] = derive_display_name_from_days(lesson_days, start_time)
        elif display_name_is_manual and display_name is not None:
            attrs['display_name'] = (display_name or '').strip() or None

        return attrs

    def create(self, validated_data):
        from decimal import Decimal
        lesson_days = validated_data.get('days_of_week')
        if not lesson_days:
            validated_data['days_of_week'] = [2, 4]
            lesson_days = [2, 4]
        display_name_is_manual = validated_data.get('display_name_is_manual', False)
        if not display_name_is_manual:
            start_time = validated_data.get('start_time')
            validated_data['display_name'] = derive_display_name_from_days(lesson_days, start_time)
        elif not validated_data.get('display_name'):
            validated_data['display_name'] = validated_data.get('name')
        
        # Set default monthly_fee and monthly_lessons_count if not provided
        if 'monthly_fee' not in validated_data or validated_data.get('monthly_fee') is None:
            validated_data['monthly_fee'] = None  # Allow None, teacher sets it later
        if 'monthly_lessons_count' not in validated_data:
            validated_data['monthly_lessons_count'] = 8  # Default 8 lessons per month
        
        return super().create(validated_data)

    def update(self, instance, validated_data):
        display_name_is_manual = validated_data.get('display_name_is_manual', instance.display_name_is_manual)
        lesson_days = validated_data.get('days_of_week')
        if lesson_days is None:
            lesson_days = instance.days_of_week or []

        if not display_name_is_manual and lesson_days:
            start_time = validated_data.get('start_time') or instance.start_time
            validated_data['display_name'] = derive_display_name_from_days(lesson_days, start_time)
        elif display_name_is_manual and 'display_name' not in validated_data:
            pass

        return super().update(instance, validated_data)


class GroupStudentSerializer(serializers.ModelSerializer):
    """Group Student serializer"""
    student_profile = StudentProfileSerializer(read_only=True)
    student_profile_id = serializers.IntegerField(write_only=True, required=False)
    
    class Meta:
        model = GroupStudent
        fields = ['id', 'group', 'student_profile', 'student_profile_id', 'active', 'joined_at']
        read_only_fields = ['id', 'joined_at']
