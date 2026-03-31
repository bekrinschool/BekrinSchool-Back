"""
Teacher-only credentials registry API.
List, filter, export, and reveal (with audit) imported credentials.
"""
import csv
import io
from django.db import models
from django.db.models import Q, Prefetch
from django.contrib.auth import get_user_model

from core.utils import belongs_to_user_organization

User = get_user_model()
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import IsTeacher
from students.models import ImportedCredentialRecord
from groups.models import GroupStudent


class CredentialsPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def _credentials_queryset(request, group_id=None, search=None):
    org = request.user.organization
    # Prefetch group memberships to avoid N+1 in _record_to_dict (groups list)
    active_memberships = GroupStudent.objects.filter(
        active=True, left_at__isnull=True
    ).select_related("group")
    qs = (
        ImportedCredentialRecord.objects.filter(student__organization=org)
        .select_related("student", "student__student_profile", "parent", "created_by")
        .prefetch_related(
            Prefetch(
                "student__student_profile__group_memberships",
                queryset=active_memberships,
            )
        )
        .order_by("-created_at")
    )

    if group_id:
        qs = qs.filter(
            student__student_profile__group_memberships__group_id=group_id,
            student__student_profile__group_memberships__active=True,
            student__student_profile__group_memberships__left_at__isnull=True,
        ).distinct()

    if search:
        s = search.strip()
        qs = qs.filter(
            Q(student_full_name__icontains=s)
            | Q(student_email__icontains=s)
            | Q(parent_email__icontains=s)
        )

    return qs


def _record_to_dict(rec, include_password=False):
    from students.credential_crypto import decrypt_credentials

    # Use prefetched group_memberships to avoid N+1
    try:
        profile = rec.student.student_profile
        groups = [m.group.name for m in profile.group_memberships.all()]
    except Exception:
        groups = []
    data = {
        "id": rec.id,
        "studentFullName": rec.student_full_name,
        "grade": rec.grade,
        "studentEmail": rec.student_email,
        "parentEmail": rec.parent_email or "",
        "groups": groups,
        "createdAt": rec.created_at.isoformat(),
        "createdByTeacher": rec.created_by.full_name if rec.created_by else None,
    }
    if include_password and rec.initial_password_encrypted:
        try:
            pw = decrypt_credentials(rec.initial_password_encrypted)
            data["studentPassword"] = pw["student_password"]
            data["parentPassword"] = pw["parent_password"]
        except Exception:
            data["studentPassword"] = ""
            data["parentPassword"] = ""
    return data


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def credentials_list_view(request):
    """
    GET /api/teacher/credentials?group_id=&search=&page=&page_size=
    """
    group_id = request.query_params.get("group_id")
    if group_id:
        try:
            group_id = int(group_id)
        except ValueError:
            group_id = None
    search = request.query_params.get("search")

    qs = _credentials_queryset(request, group_id=group_id, search=search)
    paginator = CredentialsPagination()
    page = paginator.paginate_queryset(qs, request)
    results = [_record_to_dict(r, include_password=False) for r in page]
    return paginator.get_paginated_response(results)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def credentials_reveal_view(request, pk):
    """
    POST /api/teacher/credentials/<id>/reveal
    Returns decrypted passwords. Updates password_viewed_at.
    """
    org = request.user.organization
    try:
        rec = ImportedCredentialRecord.objects.select_related("student").get(pk=pk)
    except ImportedCredentialRecord.DoesNotExist:
        return Response({"detail": "Record not found"}, status=status.HTTP_404_NOT_FOUND)

    if not belongs_to_user_organization(rec.student, request.user, "organization"):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    rec.password_viewed_at = timezone.now()
    rec.save(update_fields=["password_viewed_at"])

    return Response(_record_to_dict(rec, include_password=True))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def user_reveal_password_view(request, user_id):
    """
    POST /api/teacher/users/<user_id>/reveal-password
    One-time reveal. Returns password if not yet revealed. Sets password_viewed_at.
    """
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return Response({"detail": "İstifadəçi tapılmadı"}, status=status.HTTP_404_NOT_FOUND)
    org = request.user.organization
    if not belongs_to_user_organization(user, request.user, "organization"):
        return Response({"detail": "İcazə yoxdur"}, status=status.HTTP_403_FORBIDDEN)
    rec = ImportedCredentialRecord.objects.filter(
        models.Q(student=user) | models.Q(parent=user)
    ).first()
    if not rec:
        return Response({"detail": "Parol qeydiyyatı tapılmadı. İmport və ya əl ilə yaradılan hesablar üçün mövcuddur."}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(rec.student, request.user, "organization"):
        return Response({"detail": "İcazə yoxdur"}, status=status.HTTP_403_FORBIDDEN)
    if rec.password_viewed_at:
        return Response({
            "detail": "Parol artıq göstərilib. Yeni parol yaratmaq üçün 'Reset + Show once' istifadə edin.",
            "revealed": False,
        }, status=status.HTTP_400_BAD_REQUEST)
    rec.password_viewed_at = timezone.now()
    rec.save(update_fields=["password_viewed_at"])
    from students.credential_crypto import decrypt_credentials
    pw = decrypt_credentials(rec.initial_password_encrypted)
    password = pw.get("student_password") or pw.get("parent_password") or ""
    return Response({
        "password": password,
        "revealed": True,
        "message": "Parol yalnız bir dəfə göstərilir.",
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def user_reset_password_view(request, user_id):
    """
    POST /api/teacher/users/<user_id>/reset-password
    Reset password, return new password once. Updates user and ImportedCredentialRecord.
    """
    from students.credentials import generate_simple_password
    from students.credential_crypto import encrypt_credentials

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return Response({"detail": "İstifadəçi tapılmadı"}, status=status.HTTP_404_NOT_FOUND)
    org = request.user.organization
    if not belongs_to_user_organization(user, request.user, "organization"):
        return Response({"detail": "İcazə yoxdur"}, status=status.HTTP_403_FORBIDDEN)
    rec = ImportedCredentialRecord.objects.filter(
        models.Q(student=user) | models.Q(parent=user)
    ).select_related("student", "parent").first()
    new_password = generate_simple_password()
    user.set_password(new_password)
    user.save(update_fields=["password"])
    if rec:
        encrypted = encrypt_credentials(new_password, new_password)
        rec.initial_password_encrypted = encrypted
        rec.password_viewed_at = None
        rec.save(update_fields=["initial_password_encrypted", "password_viewed_at"])
        other_user = rec.parent if user == rec.student else rec.student
        if other_user:
            other_user.set_password(new_password)
            other_user.save(update_fields=["password"])
    return Response({
        "password": new_password,
        "message": "Yeni parol yaradıldı. Yalnız bir dəfə göstərilir.",
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def credentials_export_view(request):
    """
    GET /api/teacher/credentials/export.csv?group_id=&search=
    """
    group_id = request.query_params.get("group_id")
    if group_id:
        try:
            group_id = int(group_id)
        except ValueError:
            group_id = None
    search = request.query_params.get("search")

    qs = _credentials_queryset(request, group_id=group_id, search=search)
    # Export without pagination
    qs = qs[:5000]

    from students.credential_crypto import decrypt_credentials

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "fullName", "grade", "studentEmail", "studentPassword",
        "parentEmail", "parentPassword", "groups", "createdAt", "createdBy",
    ])
    for rec in qs:
        pw = {"student_password": "", "parent_password": ""}
        if rec.initial_password_encrypted:
            try:
                pw = decrypt_credentials(rec.initial_password_encrypted)
            except Exception:
                pass
        groups = ", ".join(
            GroupStudent.objects.filter(
                student_profile=rec.student.student_profile,
                active=True,
                left_at__isnull=True,
            ).values_list("group__name", flat=True)
        )
        writer.writerow([
            rec.student_full_name,
            rec.grade or "",
            rec.student_email,
            pw["student_password"],
            rec.parent_email or "",
            pw["parent_password"],
            groups,
            rec.created_at.strftime("%Y-%m-%d %H:%M"),
            rec.created_by.full_name if rec.created_by else "",
        ])

    response = HttpResponse(
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = 'attachment; filename="credentials_export.csv"'
    return response
