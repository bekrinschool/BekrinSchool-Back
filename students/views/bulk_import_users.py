"""
Bulk import users (Student + Parent) from CSV with new format.
CSV fields: fullName, grade, studentEmail, parentEmail, password (optional).
Same password for both accounts. Auto-generate if password empty.
"""
import csv
import io
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import IsTeacher
from core.utils import belongs_to_user_organization
from students.models import StudentProfile, ParentProfile, ParentChild, ImportedCredentialRecord
from students.credential_crypto import encrypt_credentials
from students.credentials import generate_simple_password



User = get_user_model()

REQUIRED_HEADERS = ["fullname", "grade", "studentemail", "parentemail", "password"]
HEADER_ALIASES = {
    "fullname": ["fullname", "full_name", "fullname"],
    "grade": ["grade", "class"],
    "studentemail": ["studentemail", "student_email", "studentemail"],
    "parentemail": ["parentemail", "parent_email", "parentemail"],
    "password": ["password"],
}


def _normalize_header(h):
    return (h or "").strip().lower().replace(" ", "").replace("_", "")


def _map_headers(fieldnames):
    """Map CSV headers to canonical keys. Case-insensitive. Returns dict col_name -> canonical."""
    normalized = {_normalize_header(h): h for h in (fieldnames or [])}
    result = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for a in aliases:
            key = _normalize_header(a)
            if key in normalized:
                result[canonical] = normalized[key]
                break
    return result


def _parse_csv_rows(content):
    """Parse CSV. Returns (rows as list of dicts, error message or None)."""
    try:
        reader = csv.DictReader(io.StringIO(content))
        fieldnames = reader.fieldnames or []
        mapping = _map_headers(fieldnames)
        required = ["fullname", "grade", "studentemail", "parentemail", "password"]
        missing = [r for r in required if r not in mapping]
        if missing:
            return None, f"CSV-də bu sütunlar tələb olunur: fullName, grade, studentEmail, parentEmail, password"
        rows = []
        for i, raw in enumerate(reader, start=2):
            row = {k: (raw.get(mapping[k]) or "").strip() for k in mapping}
            row["_line"] = i
            rows.append(row)
        return rows, None
    except Exception as e:
        return None, str(e)


def _validate_row(row, seen_emails, existing_emails):
    """Returns (ok, error_message)."""
    full_name = (row.get("fullname") or "").strip()
    if not full_name or len(full_name) < 2:
        return False, "fullName tələb olunur (min 2 simvol)"
    if len(full_name) > 255:
        return False, "fullName çox uzundur"

    student_email = (row.get("studentemail") or "").strip().lower()
    parent_email = (row.get("parentemail") or "").strip().lower()

    if not student_email:
        return False, "studentEmail tələb olunur"
    if "@" not in student_email:
        return False, "studentEmail düzgün formatda deyil"
    if not parent_email:
        return False, "parentEmail tələb olunur"
    if "@" not in parent_email:
        return False, "parentEmail düzgün formatda deyil"

    pw = (row.get("password") or "").strip()
    if pw and len(pw) < 6:
        return False, "Şifrə minimum 6 simvol olmalıdır"

    if student_email in seen_emails or parent_email in seen_emails:
        return False, "Email faylda təkrarlanır"
    seen_emails.add(student_email)
    seen_emails.add(parent_email)

    if student_email in existing_emails or parent_email in existing_emails:
        return False, "Email artıq bazada mövcuddur"

    return True, None


def _create_pair_optimized(org, row, used_passwords, created_by):
    """
    Optimized: Create Student + Parent pair without nested transaction.
    Returns (creds_dict, None) on success or (None, error_msg) on failure.
    """
    full_name = (row.get("fullname") or "").strip()
    grade = (row.get("grade") or "").strip() or None
    student_email = (row.get("studentemail") or "").strip().lower()
    parent_email = (row.get("parentemail") or "").strip().lower()
    pw_raw = (row.get("password") or "").strip()

    if pw_raw:
        password = pw_raw
    else:
        password = generate_simple_password(used_passwords)
        if not password:
            return None, "Şifrə yaradıla bilmədi"
        used_passwords.add(password)

    try:
        student_user = User.objects.create_user(
            email=student_email,
            password=password,
            full_name=full_name,
            role="student",
            is_active=True,
            organization=org,
            must_change_password=True,
        )
        # Signal auto-creates StudentProfile, so update it
        student_profile, _ = StudentProfile.objects.get_or_create(
            user=student_user,
            defaults={'grade': grade, 'balance': Decimal('0.00')}
        )
        if not _:
            student_profile.grade = grade
            student_profile.save(update_fields=['grade'])

        parent_user = User.objects.create_user(
            email=parent_email,
            password=password,
            full_name=f"{full_name} — Valideyn",
            role="parent",
            is_active=True,
            organization=org,
            must_change_password=True,
        )
        ParentProfile.objects.get_or_create(user=parent_user)
        ParentChild.objects.get_or_create(parent=parent_user, student=student_user)

        encrypted = encrypt_credentials(password, password)
        ImportedCredentialRecord.objects.create(
            created_by=created_by,
            source=ImportedCredentialRecord.SOURCE_CSV_IMPORT,
            student=student_user,
            parent=parent_user,
            student_full_name=full_name,
            student_email=student_email,
            parent_email=parent_email,
            grade=grade,
            initial_password_encrypted=encrypted,
            password_is_one_time=True,
        )
    except Exception as e:
        return None, str(e)

    return {
        "fullName": full_name,
        "studentEmail": student_email,
        "parentEmail": parent_email,
        "password": password,
    }, None


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def bulk_import_template_csv_view(request):
    """
    GET /api/teacher/bulk-import/template-csv
    Download sample CSV template.
    """
    sample = """fullName,grade,studentEmail,parentEmail,password
Ayşən Əliyeva,5A,aysen.aliyeva@bekrin.com,ali.aliyev@bekrin.com,Aysen123
Məmməd Həsənov,6B,mammad.hasanov@bekrin.com,melik.hasanov@bekrin.com,"""
    response = HttpResponse("\ufeff" + sample, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="bulk_import_template.csv"'
    return response


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def bulk_import_users_view(request):
    """
    POST /api/teacher/bulk-import/users
    Body: multipart (file?) or JSON/form (csvText?)
    File has priority over csvText.
    Creates Student + Parent per row. Same password. Returns created, skipped, errors.
    """
    try:
        content = None
        if request.FILES.get("file"):
            f = request.FILES["file"]
            if not (f.name or "").lower().endswith(".csv"):
                return Response({"detail": "Fayl CSV formatında olmalıdır"}, status=status.HTTP_400_BAD_REQUEST)
            try:
                content = f.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                return Response({"detail": "CSV UTF-8 encoding olmalıdır"}, status=status.HTTP_400_BAD_REQUEST)
        elif request.data.get("csvText"):
            content = request.data.get("csvText", "")
        else:
            return Response(
                {"detail": "CSV faylı və ya csvText sahəsi təqdim edin"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rows, err = _parse_csv_rows(content)
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({"detail": "CSV-də məlumat sətiri yoxdur"}, status=status.HTTP_400_BAD_REQUEST)

        org = request.user.organization
        if not org:
            return Response(
                {"detail": "İstifadəçinin təşkilatı yoxdur"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        existing_emails = set(
            User.objects.filter(organization=org).values_list("email", flat=True)
        )
        existing_emails = {e.lower() for e in existing_emails if e}

        seen_emails = set()
        to_create = []
        errors_list = []

        for row in rows:
            ok, msg = _validate_row(row, seen_emails, existing_emails)
            if ok:
                to_create.append(row)
            else:
                errors_list.append({"row": row.get("_line", 0), "field": "row", "message": msg})

        created = 0
        skipped = 0
        used_passwords = set()
        created_creds = []

        # Process in batches for better performance (one transaction per batch)
        BATCH_SIZE = 50
        for i in range(0, len(to_create), BATCH_SIZE):
            batch = to_create[i:i + BATCH_SIZE]
            batch_emails = set()
            for row in batch:
                batch_emails.add((row.get("studentemail") or "").strip().lower())
                batch_emails.add((row.get("parentemail") or "").strip().lower())
            
            # Check all emails in batch at once
            existing_batch_emails = set(
                User.objects.filter(email__in=batch_emails).values_list("email", flat=True)
            )
            existing_batch_emails = {e.lower() for e in existing_batch_emails if e}
            
            with transaction.atomic():
                for row in batch:
                    student_email = (row.get("studentemail") or "").strip().lower()
                    parent_email = (row.get("parentemail") or "").strip().lower()

                    # Check if emails exist in this batch or were already created
                    if student_email in existing_batch_emails or parent_email in existing_batch_emails:
                        skipped += 1
                        errors_list.append({"row": row.get("_line"), "field": "email", "message": "Email artıq mövcuddur"})
                        continue

                    try:
                        creds, err = _create_pair_optimized(org, row, used_passwords, request.user)
                        if creds:
                            created += 1
                            created_creds.append(creds)
                            existing_emails.add(student_email)
                            existing_emails.add(parent_email)
                            existing_batch_emails.add(student_email)
                            existing_batch_emails.add(parent_email)
                        else:
                            skipped += 1
                            errors_list.append({"row": row.get("_line"), "field": "create", "message": err or "Xəta"})
                    except Exception as e:
                        skipped += 1
                        error_msg = str(e)
                        if settings.DEBUG:
                            import traceback
                            error_msg += f" | {traceback.format_exc()}"
                        errors_list.append({"row": row.get("_line"), "field": "create", "message": f"Xəta: {error_msg}"})

        return Response(
            {
                "created": created,
                "skipped": skipped,
                "errors": errors_list[:100],
                "credentials": created_creds,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        import traceback
        error_msg = str(e)
        if settings.DEBUG:
            error_msg += f"\n{traceback.format_exc()}"
        return Response(
            {"detail": f"Gözlənilməz xəta: {error_msg}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
