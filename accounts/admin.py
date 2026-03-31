"""
Admin configuration for accounts app
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm, ReadOnlyPasswordHashField
from django import forms
from .models import User


def _validate_org_for_role(cleaned_data):
    role = cleaned_data.get('role')
    org = cleaned_data.get('organization')
    if role in ('student', 'parent') and not org:
        raise forms.ValidationError(
            {'organization': 'Student və Parent istifadəçiləri üçün təşkilat tələb olunur.'}
        )


class UserAdminForm(forms.ModelForm):
    """
    Change form with proper password handling.
    Password is read-only (hash display only). Use "Change password" link to set new password.
    Plain text password must NEVER be editable here - that overwrites the hash and breaks login.
    """
    password = ReadOnlyPasswordHashField(
        label='Şifrə',
        help_text=(
            'Xam şifrələr saxlanılmır. Şifrəni dəyişmək üçün '
            '"Şifrəni dəyiş" (Change password) linkindən istifadə edin.'
        ),
    )

    class Meta:
        model = User
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        _validate_org_for_role(cleaned)
        return cleaned


class UserAddForm(UserCreationForm):
    """Add form with org validation."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email', 'full_name', 'role', 'organization', 'phone')

    def clean(self):
        cleaned = super().clean()
        _validate_org_for_role(cleaned)
        return cleaned


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Custom User Admin"""
    form = UserAdminForm
    add_form = UserAddForm
    list_display = ['email', 'full_name', 'role', 'organization', 'is_active', 'is_staff', 'date_joined']
    list_filter = ['role', 'is_active', 'is_staff', 'is_superuser', 'organization', 'date_joined']
    search_fields = ['email', 'full_name']
    ordering = ['-date_joined']
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('full_name', 'role', 'organization', 'phone')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'must_change_password', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined', 'updated_at')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'full_name', 'role', 'organization', 'phone', 'password1', 'password2', 'is_active', 'is_staff'),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs['form'] = self.add_form
        return super().get_form(request, obj, **kwargs)
    
    readonly_fields = ['date_joined', 'updated_at', 'last_login']
