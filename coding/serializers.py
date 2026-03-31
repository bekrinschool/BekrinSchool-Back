"""
Serializers for coding app
"""
from rest_framework import serializers
from .models import CodingTask, CodingTestCase, CodingTopic


class CodingTopicSerializer(serializers.ModelSerializer):
    """Coding topic for dropdowns."""

    class Meta:
        model = CodingTopic
        fields = ['id', 'name']


class CodingTopicCreateSerializer(serializers.ModelSerializer):
    """Create coding topic."""

    class Meta:
        model = CodingTopic
        fields = ['name']

    def create(self, validated_data):
        request = self.context.get('request')
        org = getattr(request.user, 'organization_id', None) if request else None
        created_by = request.user if request else None
        return CodingTopic.objects.create(
            organization_id=org,
            created_by=created_by,
            **validated_data,
        )


class CodingTaskSerializer(serializers.ModelSerializer):
    """Coding Task serializer for list/detail"""
    topic_name = serializers.CharField(source='topic.name', read_only=True, allow_null=True)

    class Meta:
        model = CodingTask
        fields = [
            'id', 'topic', 'topic_name', 'title', 'description', 'difficulty',
            'starter_code', 'points', 'is_active', 'order_index', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class CodingTaskCreateSerializer(serializers.ModelSerializer):
    """Coding Task create/update serializer. Topic required on create."""
    topic = serializers.PrimaryKeyRelatedField(
        queryset=CodingTopic.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        if self.instance is None and attrs.get('topic') is None:
            raise serializers.ValidationError({'topic': 'Mövzu tələb olunur.'})
        return attrs

    class Meta:
        model = CodingTask
        fields = [
            'topic', 'title', 'description', 'difficulty', 'starter_code',
            'points', 'is_active', 'order_index'
        ]

    def create(self, validated_data):
        request = self.context.get('request')
        org = getattr(request.user, 'organization', None) if request else None
        validated_data['organization'] = org
        return super().create(validated_data)


class CodingTestCaseSerializer(serializers.ModelSerializer):
    """Test case for list/detail. Expose expected as expected_output in API. is_sample for Run visibility."""
    expected_output = serializers.CharField(source='expected', read_only=True)

    class Meta:
        model = CodingTestCase
        fields = ['id', 'input_data', 'expected', 'expected_output', 'explanation', 'order_index', 'is_sample', 'created_at']
        read_only_fields = ['id', 'created_at']


class CodingTestCaseCreateSerializer(serializers.ModelSerializer):
    """Test case create/update. Accept expected_output or expected. is_sample: True = shown in Run."""
    expected_output = serializers.CharField(required=False, allow_blank=True)
    expected = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = CodingTestCase
        fields = ['input_data', 'expected', 'expected_output', 'explanation', 'order_index', 'is_sample']

    def validate(self, attrs):
        expected = attrs.get('expected') or attrs.get('expected_output')
        if expected is None:
            raise serializers.ValidationError('expected or expected_output is required')
        attrs['expected'] = expected
        return attrs

    def create(self, validated_data):
        validated_data.pop('expected_output', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('expected_output', None)
        return super().update(instance, validated_data)
