"""
Serializers for payments app
"""
from decimal import Decimal
from rest_framework import serializers
from django.core.validators import MinValueValidator
from .models import Payment
from students.utils import get_teacher_display_balance


class PaymentSerializer(serializers.ModelSerializer):
    """Payment serializer. Exposes date from payment_date for frontend."""
    studentId = serializers.IntegerField(source='student_profile.id', read_only=True)
    studentName = serializers.CharField(source='student_profile.user.full_name', read_only=True)
    groupId = serializers.IntegerField(source='group.id', read_only=True, allow_null=True)
    groupName = serializers.CharField(source='group.name', read_only=True, allow_null=True)
    paymentNumber = serializers.CharField(source='receipt_no', read_only=True)
    sequenceNumber = serializers.IntegerField(source='sequence_number', read_only=True, allow_null=True)
    date = serializers.DateField(source='payment_date', read_only=True)
    
    class Meta:
        model = Payment
        fields = [
            'id', 'studentId', 'studentName', 'groupId', 'groupName',
            'amount', 'date', 'title', 'method', 'status', 'note', 'paymentNumber', 'sequenceNumber'
        ]
        read_only_fields = ['id', 'paymentNumber', 'sequenceNumber']
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if 'amount' in data and data['amount'] is not None:
            data['amount'] = float(data['amount'])
        # Add student balance display for teacher view
        if hasattr(instance, 'student_profile') and instance.student_profile:
            data['studentBalance'] = float(instance.student_profile.balance)
            data['studentDisplayBalanceTeacher'] = get_teacher_display_balance(instance.student_profile.balance)
        return data


class TeacherPaymentSerializer(PaymentSerializer):
    """
    Same as PaymentSerializer - returns real amount.
    Frontend applies amount/4 for teacher display via formatPaymentDisplay.
    Also includes updated student balance info.
    """
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Add student balance info after payment
        if hasattr(instance, 'student_profile') and instance.student_profile:
            student = instance.student_profile
            # Refresh to get latest balance
            student.refresh_from_db()
            data['studentBalance'] = float(student.balance)
            data['studentDisplayBalanceTeacher'] = get_teacher_display_balance(student.balance)
        return data


class _NullableIntegerField(serializers.IntegerField):
    """Accepts empty string as None for optional IDs from frontend."""

    def to_internal_value(self, data):
        if data in (None, '', []) or (isinstance(data, str) and not str(data).strip()):
            return None
        if isinstance(data, str) and str(data).strip().isdigit():
            return int(str(data).strip())
        return super().to_internal_value(data)


class PaymentCreateSerializer(serializers.Serializer):
    """Payment create serializer (frontend format)"""
    studentId = serializers.IntegerField()
    groupId = _NullableIntegerField(required=False, allow_null=True)
    amount = serializers.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    date = serializers.DateField()
    title = serializers.CharField(required=False, allow_blank=True)
    method = serializers.ChoiceField(choices=['cash', 'card', 'bank'])
    status = serializers.ChoiceField(choices=['paid', 'pending'])
    note = serializers.CharField(required=False, allow_blank=True)
    
    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than 0")
        return value
    
    def create(self, validated_data):
        import logging
        from decimal import Decimal
        from django.db import transaction
        from students.models import StudentProfile
        from groups.models import Group
        from notifications.services import auto_resolve_balance_notifications
        from students.models import BalanceLedger
        from students.utils import get_teacher_display_balance
        
        logger = logging.getLogger(__name__)
        
        student_id = validated_data.pop('studentId')
        group_id = validated_data.pop('groupId', None)
        date_val = validated_data.pop('date')
        title_val = validated_data.pop('title', None)
        amount = validated_data.get('amount')
        status_val = validated_data.get('status', 'paid')
        
        student = StudentProfile.objects.select_related('user').get(id=student_id)
        group = Group.objects.get(id=group_id) if group_id else None
        created_by = validated_data.pop('created_by', None)
        organization = validated_data.pop('organization', None) or (created_by.organization if created_by else None)
        
        # Ensure student belongs to same org when org is set
        if organization and getattr(student.user, 'organization_id', None) != organization.pk:
            raise serializers.ValidationError(
                {'studentId': 'Bu şagird sizin təşkilatınıza aid deyil'}
            )

        # Log before update
        old_balance = student.balance or Decimal('0')
        logger.info(f"[PAYMENT] Creating payment: student_id={student_id}, amount={amount}, status={status_val}, old_balance={old_balance}")

        with transaction.atomic():
            # Generate safe sequential payment number using select_for_update
            from django.db.models import Max
            from django.db import transaction
            
            # Lock the table row to prevent concurrent sequence number generation
            max_seq = Payment.objects.select_for_update().aggregate(
                max_seq=Max('sequence_number')
            )['max_seq'] or 0
            next_sequence = max_seq + 1
            
            # Refresh student to get latest balance
            student.refresh_from_db()
            old_balance = student.balance or Decimal('0')
            
            # Create payment with sequence number
            payment = Payment.objects.create(
                student_profile=student,
                group=group,
                payment_date=date_val,
                title=title_val or '',
                created_by=created_by,
                organization=organization,
                sequence_number=next_sequence,
                **validated_data
            )
            
            # Update student balance (only if status is 'paid')
            if status_val == 'paid' and amount:
                amount_decimal = Decimal(str(amount))
                new_balance = old_balance + amount_decimal
                
                logger.info(f"[PAYMENT] Updating balance: student_id={student_id}, old={old_balance}, amount={amount_decimal}, new={new_balance}")
                
                student.balance = new_balance
                student.save(update_fields=['balance'])
                
                # Verify update
                student.refresh_from_db()
                logger.info(f"[PAYMENT] Verified balance after save: student_id={student_id}, balance={student.balance}")
                
                # Create ledger entry
                BalanceLedger.objects.create(
                    student_profile=student,
                    group=group,
                    date=date_val,
                    amount_delta=amount_decimal,
                    reason=BalanceLedger.REASON_TOPUP,
                )
                
                # Auto-resolve balance zero notifications
                resolved_count = auto_resolve_balance_notifications(student)
                logger.info(f"[PAYMENT] Auto-resolved {resolved_count} notifications for student_id={student_id}")
            else:
                logger.info(f"[PAYMENT] Skipping balance update: status={status_val}, amount={amount}")
        
        return payment
