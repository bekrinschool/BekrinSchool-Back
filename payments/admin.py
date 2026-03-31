"""
Admin configuration for payments app
"""
from django.contrib import admin
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Payment Admin"""
    list_display = ['receipt_no', 'student_profile', 'group', 'amount', 'payment_date', 'method', 'status', 'created_by']
    list_filter = ['status', 'method', 'payment_date', 'created_at']
    search_fields = ['receipt_no', 'student_profile__user__email', 'student_profile__user__full_name']
    readonly_fields = ['receipt_no', 'created_at', 'updated_at']
    ordering = ['-payment_date', '-created_at']
