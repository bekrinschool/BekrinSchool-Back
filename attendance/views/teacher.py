"""
Teacher attendance API.
Endpoints:
- GET  /attendance/group/{group_id}/daily?date=      Daily view: students + status for date
- POST /attendance/save                              Bulk save attendance
- GET  /attendance/group/{group_id}/monthly?month=&year=  Monthly stats per student
- GET  /attendance/grid?groupId=&from=&to=           Lesson-dates grid (dates from group.lesson_days)
- POST /attendance/bulk-upsert                       Bulk upsert attendance records
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsTeacher
from core.utils import filter_by_organization, belongs_to_user_organization
import logging
from groups.models import Group
from groups.services import get_active_students_for_group
from students.models import StudentProfile
from attendance.models import AttendanceRecord
from attendance.services.lesson_charge import maybe_open_session_and_charge
from attendance.services.lesson_finalize import finalize_lesson_and_charge
from attendance.models import LessonHeld

logger = logging.getLogger(__name__)


VALID_STATUSES = {"present", "absent", "late", "excused"}
DEFAULT_STATUS = "present"
VALID_ENTRY_STATES = {"DRAFT", "CONFIRMED"}

def _teacher_owns_group(user, group: Group) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    return getattr(group, "created_by_id", None) == getattr(user, "id", None)


def _lesson_dates_in_range(from_date, to_date, days_of_week):
    """
    Return list of dates in [from_date, to_date] whose weekday matches days_of_week.
    days_of_week: [1..7] with 1=Mon, 7=Sun.
    Python weekday: 0=Mon, 6=Sun => our N = weekday + 1.
    Returns dates in descending order (newest first).
    """
    if not days_of_week:
        return []
    valid = set(int(d) for d in days_of_week if 1 <= int(d) <= 7)
    if not valid:
        return []
    result = []
    d = to_date
    while d >= from_date:
        our_day = d.weekday() + 1
        if our_day in valid:
            result.append(d)
        d -= timedelta(days=1)
    return result


def _lesson_dates_in_month(year, month, days_of_week):
    """Return lesson dates in month (ascending order)."""
    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)
    dates_desc = _lesson_dates_in_range(start_date, end_date, days_of_week)
    return list(reversed(dates_desc))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_group_daily_view(request, group_id):
    """
    GET /api/teacher/attendance/group/{group_id}/daily?date=2025-02-07
    Returns students in group with status for that date.
    """
    date_str = request.query_params.get("date")
    if not date_str:
        return Response(
            {"detail": "date query param required (YYYY-MM-DD)"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        target_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return Response(
            {"detail": "Invalid date format"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if target_date > date.today():
        return Response(
            {"detail": "Gələcək tarix üçün davamiyyət qeyd edilə bilməz"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user) or not _teacher_owns_group(request.user, group):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    memberships = get_active_students_for_group(group)
    active_students = [m.student_profile for m in memberships if not m.student_profile.is_deleted]

    # Keep history tied to historical group/date:
    # include students that have attendance record for this group+date even if moved out later.
    history_student_ids = list(
        AttendanceRecord.objects.filter(
            group=group,
            lesson_date=target_date,
        ).values_list("student_profile_id", flat=True)
    )
    history_students = list(
        StudentProfile.objects.filter(id__in=history_student_ids, is_deleted=False).select_related("user")
    )
    by_id = {s.id: s for s in active_students}
    for hs in history_students:
        by_id[hs.id] = hs
    students = list(by_id.values())
    if not students:
        return Response(
            {"detail": "Bu qrupda şagird yoxdur"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Fetch existing records for this group+date (unique per student+group+date)
    student_ids = [s.id for s in students]
    records = AttendanceRecord.objects.filter(
        student_profile_id__in=student_ids,
        lesson_date=target_date,
        group=group,
    ).select_related("student_profile")

    record_map = {r.student_profile_id: {"status": r.status, "entry_state": r.entry_state} for r in records}

    result = {
        "date": target_date.isoformat(),
        "groupId": str(group.id),
        "groupName": group.name,
        "students": [],
    }
    for sp in students:
        rec = record_map.get(sp.id) or {}
        result["students"].append({
            "id": str(sp.id),
            "fullName": rec.get("student_name_snapshot") or sp.user.full_name,
            "email": sp.user.email,
            "status": rec.get("status", DEFAULT_STATUS),
            "entryState": rec.get("entry_state", "DRAFT"),
        })

    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_save_view(request):
    """
    POST /api/teacher/attendance/save
    Body: { date: "YYYY-MM-DD", groupId: "…", records: [{ studentId, status }], finalize: true/false }
    Creates or updates attendance in transaction.
    If finalize=true, finalizes the lesson and charges students (idempotent).
    """
    date_str = request.data.get("date")
    group_id = request.data.get("groupId")
    records_data = request.data.get("records", [])
    finalize = request.data.get("finalize", False)
    requested_entry_state = str(request.data.get("entry_state") or "").upper()
    entry_state = "CONFIRMED" if finalize else (requested_entry_state if requested_entry_state in VALID_ENTRY_STATES else "DRAFT")

    if not date_str or not group_id:
        return Response(
            {"detail": "date and groupId are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        target_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return Response(
            {"detail": "Invalid date format"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if target_date > date.today():
        return Response(
            {"detail": "Gələcək tarix üçün davamiyyət saxlanıla bilməz"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user) or not _teacher_owns_group(request.user, group):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
    if group.student_count <= 0:
        return Response(
            {"detail": "Bu qrupda şagird yoxdur"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Check if lesson is finalized (locked) - prevent editing unless unlock is requested
    unlock = request.data.get("unlock", False)
    try:
        lesson_held = LessonHeld.objects.get(group=group, date=target_date)
        if lesson_held.is_finalized and not unlock:
            return Response({
                "ok": False,
                "detail": "Dərs tamamlanıb və kilidləndi. Redaktə etmək üçün kilidi açın.",
                "is_finalized": True
            }, status=status.HTTP_400_BAD_REQUEST)
    except LessonHeld.DoesNotExist:
        pass  # Lesson not finalized yet, allow editing

    with transaction.atomic():
        saved = 0
        for item in records_data:
            student_id = item.get("studentId")
            status_val = item.get("status", DEFAULT_STATUS)
            if not student_id or status_val not in VALID_STATUSES:
                continue
            try:
                student = StudentProfile.objects.get(
                    id=student_id, is_deleted=False
                )
            except StudentProfile.DoesNotExist:
                continue
            if not belongs_to_user_organization(student.user, request.user, "organization"):
                continue

            AttendanceRecord.objects.update_or_create(
                student_profile=student,
                lesson_date=target_date,
                group=group,
                defaults={
                    "status": status_val,
                    "entry_state": entry_state,
                    "group": group,
                    "group_name_snapshot": group.name,
                    "organization": request.user.organization,
                    "marked_by": request.user,
                    "marked_at": timezone.now(),
                },
            )
            saved += 1

        # If finalize=true, finalize lesson and charge (idempotent)
        lesson_finalized = False
        students_charged = 0
        charge_details = []
        
        if finalize:
            logger.info(f"[ATTENDANCE_SAVE] Finalize requested: saved={saved}, group_id={group.id}, date={target_date}")
            if not saved:
                logger.warning(f"[ATTENDANCE_SAVE] Finalize requested but no attendance records saved")
            
            try:
                lesson_finalized, students_charged, charge_details = finalize_lesson_and_charge(
                    group, target_date, created_by=request.user
                )
                
                logger.info(f"[ATTENDANCE_SAVE] Finalize result: lesson_finalized={lesson_finalized}, students_charged={students_charged}, charge_details_count={len(charge_details)}")
                
                # Log each charge detail
                for detail in charge_details:
                    logger.info(f"[ATTENDANCE_SAVE] Charge detail: studentId={detail['studentId']}, oldBalance={detail['oldBalance']}, newBalance={detail['newBalance']}, chargeAmount={detail['chargeAmount']}")
            except Exception as e:
                logger.error(f"[ATTENDANCE_SAVE] Error in finalize_lesson_and_charge: {e}", exc_info=True)
                # Don't fail the request, but log error and set defaults
                import traceback
                logger.error(f"[ATTENDANCE_SAVE] Full traceback: {traceback.format_exc()}")
                # Set defaults to prevent response errors
                lesson_finalized = False
                students_charged = 0
                charge_details = []

    # Build response with proof fields (PART 0 requirement)
    response_data = {
        "ok": True,
        "date": target_date.isoformat(),
        "groupId": str(group.id),
        "saved": saved > 0,
        "charged": lesson_finalized,
        "charged_count": students_charged,
        "delivered_marked": lesson_finalized,
        "entry_state": entry_state,
        "message": "Davamiyyət saxlanıldı" + (" və dərs yekunlaşdırıldı" if lesson_finalized else ""),
    }
    
    # Add charge details (proof fields)
    if charge_details:
        response_data["charged_students"] = charge_details
    
    logger.info(f"[ATTENDANCE_SAVE] Response: {response_data}")
    return Response(response_data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_group_monthly_view(request, group_id):
    """
    GET /api/teacher/attendance/group/{group_id}/monthly?month=2&year=2025
    Returns per-student stats: Present, Absent, Late, Excused, Attendance %
    """
    year = request.query_params.get("year", str(date.today().year))
    month = request.query_params.get("month", str(date.today().month))
    try:
        year = int(year)
        month = int(month)
    except ValueError:
        return Response(
            {"detail": "Invalid year or month"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user) or not _teacher_owns_group(request.user, group):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    memberships = get_active_students_for_group(group)
    active_students = [m.student_profile for m in memberships if not m.student_profile.is_deleted]
    history_student_ids = list(
        AttendanceRecord.objects.filter(
            group=group,
            lesson_date__gte=start_date,
            lesson_date__lte=end_date,
        ).values_list("student_profile_id", flat=True)
    )
    history_students = list(
        StudentProfile.objects.filter(id__in=history_student_ids, is_deleted=False).select_related("user")
    )
    by_id = {s.id: s for s in active_students}
    for hs in history_students:
        by_id[hs.id] = hs
    students = list(by_id.values())
    student_ids = [s.id for s in students]

    from django.db.models import Count

    records = (
        AttendanceRecord.objects.filter(
            student_profile_id__in=student_ids,
            lesson_date__gte=start_date,
            lesson_date__lte=end_date,
            group=group,
        )
        .values("student_profile", "status")
        .annotate(cnt=Count("id"))
    )

    # Build counts per student
    stats = {}
    for r in records:
        sid = r["student_profile"]
        if sid not in stats:
            stats[sid] = {"present": 0, "absent": 0, "late": 0, "excused": 0}
        stats[sid][r["status"]] = r["cnt"]

    total_days = (end_date - start_date).days + 1
    result = {
        "year": year,
        "month": month,
        "groupId": str(group.id),
        "groupName": group.name,
        "students": [],
    }
    for sp in students:
        s = stats.get(sp.id, {"present": 0, "absent": 0, "late": 0, "excused": 0})
        total = s["present"] + s["absent"] + s["late"] + s["excused"]
        pct = round((s["present"] / total * 100), 1) if total > 0 else 0
        result["students"].append({
            "id": str(sp.id),
            "fullName": sp.user.full_name,
            "email": sp.user.email,
            "present": s["present"],
            "absent": s["absent"],
            "late": s["late"],
            "excused": s["excused"],
            "attendancePercent": pct,
        })

    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_student_daily_view(request, group_id, student_id):
    """
    GET /api/teacher/attendance/group/{group_id}/student/{student_id}/daily?year=&month=
    Returns daily breakdown for a student in a month (for modal).
    """
    year = request.query_params.get("year", str(date.today().year))
    month = request.query_params.get("month", str(date.today().month))
    try:
        year = int(year)
        month = int(month)
    except ValueError:
        return Response(
            {"detail": "Invalid year or month"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user) or not _teacher_owns_group(request.user, group):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    try:
        student = StudentProfile.objects.get(id=student_id, is_deleted=False)
    except StudentProfile.DoesNotExist:
        return Response({"detail": "Student not found"}, status=status.HTTP_404_NOT_FOUND)

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    records = AttendanceRecord.objects.filter(
        student_profile=student,
        group=group,
        lesson_date__gte=start_date,
        lesson_date__lte=end_date,
    ).values_list("lesson_date", "status")

    record_map = {d.isoformat(): s for d, s in records}
    result = []
    for i in range(last_day):
        d = start_date + timedelta(days=i)
        ds = d.isoformat()
        result.append({"date": ds, "status": record_map.get(ds)})
    return Response({"studentId": str(student_id), "year": year, "month": month, "records": result})


# Legacy: keep grid view for backward compat during transition
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attendance_grid_view(request):
    """
    GET /api/teacher/attendance?year=2026&month=2
    Returns full month grid (all groups) for legacy UI.
    """
    year = request.query_params.get("year", str(date.today().year))
    month = request.query_params.get("month", str(date.today().month))
    try:
        year = int(year)
        month = int(month)
    except ValueError:
        return Response(
            {"detail": "Invalid year or month"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)
    dates_list = [start_date + timedelta(days=i) for i in range(last_day)]

    groups_qs = Group.objects.filter(is_active=True).order_by("sort_order", "name")
    groups_qs = filter_by_organization(groups_qs, request.user)
    if not request.user.is_superuser:
        groups_qs = groups_qs.filter(created_by=request.user)

    grid = {
        "year": year,
        "month": month,
        "dates": [d.isoformat() for d in dates_list],
        "groups": [],
    }

    for group in groups_qs:
        memberships = get_active_students_for_group(group)
        students = [m.student_profile for m in memberships if not m.student_profile.is_deleted]

        student_ids = [s.id for s in students]
        records = AttendanceRecord.objects.filter(
            student_profile_id__in=student_ids,
            lesson_date__gte=start_date,
            lesson_date__lte=end_date,
            group=group,
        ).select_related("student_profile")

        record_map = {(r.student_profile_id, r.lesson_date.isoformat()): r.status for r in records}

        group_data = {
            "id": str(group.id),
            "name": group.name,
            "students": [],
        }
        for sp in students:
            student_row = {
                "id": str(sp.id),
                "fullName": sp.user.full_name,
                "email": sp.user.email,
                "records": {},
            }
            for d in dates_list:
                ds = d.isoformat()
                student_row["records"][ds] = record_map.get((sp.id, ds))
            group_data["students"].append(student_row)
        grid["groups"].append(group_data)

    return Response(grid)


@api_view(["POST", "PATCH"])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attendance_update_view(request):
    """
    POST/PATCH /api/teacher/attendance/update
    Body: { groupId, studentId, date, status }
    Single record update (legacy, for grid auto-save).
    """
    group_id = request.data.get("groupId")
    student_id = request.data.get("studentId")
    lesson_date = request.data.get("date")
    status_val = request.data.get("status")

    if not all([group_id, student_id, lesson_date, status_val]):
        return Response(
            {"detail": "groupId, studentId, date, and status are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if status_val not in VALID_STATUSES:
        return Response({"detail": "Invalid status"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        target_date = datetime.strptime(str(lesson_date)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return Response(
            {"detail": "Invalid date format"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if target_date > date.today():
        return Response(
            {"detail": "Gələcək tarix üçün davamiyyət qeyd edilə bilməz"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
        student = StudentProfile.objects.get(id=student_id, is_deleted=False)
    except (Group.DoesNotExist, StudentProfile.DoesNotExist):
        return Response(
            {"detail": "Group or student not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not belongs_to_user_organization(group, request.user):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
    if not belongs_to_user_organization(student.user, request.user, "organization"):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    record, created = AttendanceRecord.objects.update_or_create(
        student_profile=student,
        group=group,
        lesson_date=target_date,
        defaults={
            "status": status_val,
            "group": group,
            "marked_by": request.user,
            "marked_at": timezone.now(),
            "organization": request.user.organization,
        },
    )
    try:
        maybe_open_session_and_charge(group, target_date)
    except Exception as e:
        logger.error(f"Error in maybe_open_session_and_charge: {e}", exc_info=True)
    from attendance.serializers import AttendanceRecordSerializer
    return Response(
        AttendanceRecordSerializer(record).data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_grid_new_view(request):
    """
    GET /api/teacher/attendance/grid?groupId=&from=YYYY-MM-DD&to=YYYY-MM-DD
    Returns lesson dates (from group.lesson_days), students, and records.
    Null-safe; never 500; empty records allowed.
    """
    group_id = request.query_params.get("groupId")
    from_str = request.query_params.get("from")
    to_str = request.query_params.get("to")

    if not group_id:
        return Response(
            {"dates": [], "students": [], "records": []},
            status=status.HTTP_200_OK,
        )

    try:
        group = Group.objects.get(id=group_id)
    except (Group.DoesNotExist, ValueError, TypeError):
        return Response(
            {"dates": [], "students": [], "records": []},
            status=status.HTTP_200_OK,
        )

    if not belongs_to_user_organization(group, request.user):
        return Response(
            {"dates": [], "students": [], "records": []},
            status=status.HTTP_200_OK,
        )

    today = date.today()
    try:
        from_date = datetime.strptime((from_str or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        from_date = today - timedelta(days=60)
    try:
        to_date = datetime.strptime((to_str or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        to_date = today

    if from_date > to_date:
        from_date, to_date = to_date, from_date

    days_of_week = getattr(group, "days_of_week", None) or []
    dates_list = _lesson_dates_in_range(from_date, to_date, days_of_week)

    memberships = get_active_students_for_group(group)
    students = [
        {"id": str(m.student_profile.id), "full_name": m.student_profile.user.full_name}
        for m in memberships
        if not m.student_profile.is_deleted
    ]

    if not students or not dates_list:
        return Response(
            {
                "dates": [d.isoformat() for d in dates_list],
                "students": students,
                "records": [],
            },
            status=status.HTTP_200_OK,
        )

    student_ids = [int(s["id"]) for s in students]
    records_qs = AttendanceRecord.objects.filter(
        student_profile_id__in=student_ids,
        lesson_date__gte=from_date,
        lesson_date__lte=to_date,
        group=group,
    ).values_list("student_profile_id", "lesson_date", "status")

    records = [
        {
            "student_id": str(sid),
            "date": d.isoformat(),
            "status": st,
        }
        for sid, d, st in records_qs
    ]

    return Response(
        {
            "dates": [d.isoformat() for d in dates_list],
            "students": students,
            "records": records,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_bulk_upsert_view(request):
    """
    POST /api/teacher/attendance/bulk-upsert
    Body: { groupId: X, items: [{ studentId, date, status }, ...] }
    Upsert (create or update) each record. Return saved count.
    """
    group_id = request.data.get("groupId")
    items = request.data.get("items", [])

    if not group_id:
        return Response(
            {"detail": "groupId is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(items, list):
        return Response(
            {"detail": "items must be an array"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
    except (Group.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    requested_entry_state = str(request.data.get("entry_state") or "DRAFT").upper()
    entry_state = requested_entry_state if requested_entry_state in VALID_ENTRY_STATES else "DRAFT"
    saved = []
    dates_touched = set()
    with transaction.atomic():
        for item in items:
            student_id = item.get("studentId")
            date_str = item.get("date")
            status_val = item.get("status", DEFAULT_STATUS)
            if not student_id or not date_str or status_val not in VALID_STATUSES:
                continue
            try:
                target_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            try:
                student = StudentProfile.objects.get(id=student_id, is_deleted=False)
            except StudentProfile.DoesNotExist:
                continue
            if not belongs_to_user_organization(student.user, request.user, "organization"):
                continue

            AttendanceRecord.objects.update_or_create(
                student_profile=student,
                lesson_date=target_date,
                group=group,
                defaults={
                    "status": status_val,
                    "entry_state": entry_state,
                    "group": group,
                    "group_name_snapshot": group.name,
                    "organization": request.user.organization,
                    "marked_by": request.user,
                    "marked_at": timezone.now(),
                },
            )
            saved.append({
                "studentId": str(student_id),
                "date": target_date.isoformat(),
                "status": status_val,
                "entry_state": entry_state,
            })
            dates_touched.add((group.id, target_date))

        if entry_state == "CONFIRMED":
            for gid, d in dates_touched:
                try:
                    g = Group.objects.get(id=gid)
                    finalize_lesson_and_charge(g, d, created_by=request.user)
                except (ValueError, TypeError, Group.DoesNotExist) as e:
                    logger.warning(f"Error finalizing lesson for group {gid}, date {d}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error in finalize_lesson_and_charge: {e}", exc_info=True)
    return Response({"saved": len(saved), "entry_state": entry_state, "items": saved}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_monthly_new_view(request):
    """
    GET /api/teacher/attendance/monthly?groupId=&month=YYYY-MM
    Returns monthly view with lesson-days-only dates, students, records, stats.
    """
    group_id = request.query_params.get("groupId")
    month_str = request.query_params.get("month", "")

    if not group_id:
        return Response(
            {"month": "", "dates": [], "students": [], "records": [], "stats": []},
            status=status.HTTP_200_OK,
        )

    try:
        group = Group.objects.get(id=group_id)
    except (Group.DoesNotExist, ValueError, TypeError):
        return Response(
            {"month": month_str, "dates": [], "students": [], "records": [], "stats": []},
            status=status.HTTP_200_OK,
        )

    if not belongs_to_user_organization(group, request.user):
        return Response(
            {"month": month_str, "dates": [], "students": [], "records": [], "stats": []},
            status=status.HTTP_200_OK,
        )

    try:
        parts = month_str.strip().split("-")
        year = int(parts[0])
        month = int(parts[1])
    except (ValueError, IndexError, TypeError):
        today = date.today()
        year = today.year
        month = today.month
        month_str = f"{year}-{month:02d}"

    days_of_week = getattr(group, "days_of_week", None) or []
    dates_list = _lesson_dates_in_month(year, month, days_of_week)

    memberships = get_active_students_for_group(group)
    students = [
        {"id": str(m.student_profile.id), "full_name": m.student_profile.user.full_name}
        for m in memberships
        if not m.student_profile.is_deleted
    ]

    if not students:
        return Response(
            {
                "month": month_str,
                "dates": [d.isoformat() for d in dates_list],
                "students": [],
                "records": [],
                "stats": [],
            },
            status=status.HTTP_200_OK,
        )

    student_ids = [s["id"] for s in students]
    sid_int = [int(x) for x in student_ids]
    start_date = date(year, month, 1)
    _, last_day = monthrange(year, month)
    end_date = date(year, month, last_day)

    records_qs = AttendanceRecord.objects.filter(
        student_profile_id__in=sid_int,
        lesson_date__gte=start_date,
        lesson_date__lte=end_date,
        group=group,
    ).values_list("student_profile_id", "lesson_date", "status", "entry_state")

    records = [
        {"student_id": str(sid), "date": d.isoformat(), "status": st, "entry_state": es}
        for sid, d, st, es in records_qs
    ]

    from django.db.models import Count
    lesson_date_set = {d for d in dates_list}
    rec_counts = (
        AttendanceRecord.objects.filter(
            student_profile_id__in=sid_int,
            lesson_date__in=lesson_date_set,
            group=group,
        )
        .values("student_profile", "status")
        .annotate(cnt=Count("id"))
    )

    stats_map = {}
    for r in rec_counts:
        sid = r["student_profile"]
        if sid not in stats_map:
            stats_map[sid] = {"present": 0, "late": 0, "absent": 0, "excused": 0}
        stats_map[sid][r["status"]] = r["cnt"]

    total_lesson_days = len(dates_list)
    stats = []
    for sp_id in sid_int:
        s = stats_map.get(sp_id, {"present": 0, "late": 0, "absent": 0, "excused": 0})
        total = s["present"] + s["late"] + s["absent"] + s["excused"]
        missed = total_lesson_days - total
        missed_percent = round((missed / total_lesson_days * 100), 1) if total_lesson_days > 0 else 0
        stats.append({
            "student_id": str(sp_id),
            "present": s["present"],
            "late": s["late"],
            "absent": s["absent"],
            "excused": s["excused"],
            "missed_count": missed,
            "missed_percent": missed_percent,
        })

    return Response(
        {
            "month": month_str,
            "dates": [d.isoformat() for d in dates_list],
            "students": students,
            "records": records,
            "stats": stats,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_mark_all_present_view(request):
    """
    POST /api/teacher/attendance/mark-all-present
    Body: { groupId: X, date: "YYYY-MM-DD" }
    Upsert all students in group for that date as status=present.
    """
    group_id = request.data.get("groupId")
    date_str = request.data.get("date")

    if not group_id or not date_str:
        return Response(
            {"detail": "groupId and date are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        target_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return Response(
            {"detail": "Invalid date format"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
    except (Group.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    memberships = get_active_students_for_group(group)
    students = [m.student_profile for m in memberships if not m.student_profile.is_deleted]

    saved = 0
    updated_records = []
    with transaction.atomic():
        for sp in students:
            if not belongs_to_user_organization(sp.user, request.user, "organization"):
                continue
            AttendanceRecord.objects.update_or_create(
                student_profile=sp,
                lesson_date=target_date,
                group=group,
                defaults={
                    "status": DEFAULT_STATUS,
                    "entry_state": "DRAFT",
                    "group": group,
                    "group_name_snapshot": group.name,
                    "organization": request.user.organization,
                    "marked_by": request.user,
                    "marked_at": timezone.now(),
                },
            )
            saved += 1
            updated_records.append({"student_id": str(sp.id), "date": target_date.isoformat(), "status": DEFAULT_STATUS})

        if saved:
            try:
                maybe_open_session_and_charge(group, target_date)
            except Exception as e:
                logger.error(f"Error in maybe_open_session_and_charge: {e}", exc_info=True)

    return Response({"saved": saved, "items": updated_records}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def lesson_finalize_view(request):
    """
    POST /api/teacher/lessons/finalize
    Body: { groupId, date: "YYYY-MM-DD" }
    Finalize lesson and charge students (except excused).
    """
    group_id = request.data.get("groupId") or request.data.get("group_id")
    date_str = request.data.get("date")
    
    if not group_id or not date_str:
        return Response({"detail": "groupId and date required"}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        target_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return Response({"detail": "Invalid date format"}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
    
    try:
        lesson_finalized, students_charged, charge_details = finalize_lesson_and_charge(
            group, target_date, created_by=request.user
        )
        return Response({
            "ok": True,
            "lesson_finalized": lesson_finalized,
            "students_charged": students_charged,
            "charge_details": charge_details,
            "message": f"Dərs yekunlaşdırıldı. {students_charged} şagird üçün balans yeniləndi."
        })
    except Exception as e:
        logger.error(f"[lesson_finalize] Error: {e}", exc_info=True)
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def lesson_unlock_view(request):
    """
    POST /api/teacher/lessons/unlock
    Body: { groupId, date: "YYYY-MM-DD" }
    Unlock finalized lesson to allow editing.
    """
    group_id = request.data.get("groupId") or request.data.get("group_id")
    date_str = request.data.get("date")
    
    if not group_id or not date_str:
        return Response({"detail": "groupId and date required"}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        target_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return Response({"detail": "Invalid date format"}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
    
    try:
        lesson_held = LessonHeld.objects.get(group=group, date=target_date)
        lesson_held.is_finalized = False
        lesson_held.save(update_fields=['is_finalized'])
        return Response({
            "ok": True,
            "message": "Dərs kilidi açıldı. İndi redaktə edə bilərsiniz."
        })
    except LessonHeld.DoesNotExist:
        return Response({"detail": "Lesson not found"}, status=status.HTTP_404_NOT_FOUND)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsTeacher])
def attendance_bulk_delete_view(request):
    """
    POST /api/teacher/attendance/bulk-delete
    Body: { groupId: X, items: [{ studentId, date }, ...] }
    Delete attendance records (to clear cell back to empty).
    """
    group_id = request.data.get("groupId")
    items = request.data.get("items", [])

    if not group_id:
        return Response(
            {"detail": "groupId is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(items, list):
        return Response(
            {"detail": "items must be an array"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        group = Group.objects.get(id=group_id)
    except (Group.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
    if not belongs_to_user_organization(group, request.user):
        return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    deleted = 0
    with transaction.atomic():
        for item in items:
            student_id = item.get("studentId")
            date_str = item.get("date")
            if not student_id or not date_str:
                continue
            try:
                target_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            try:
                student = StudentProfile.objects.get(id=student_id, is_deleted=False)
            except StudentProfile.DoesNotExist:
                continue
            if not belongs_to_user_organization(student.user, request.user, "organization"):
                continue

            n, _ = AttendanceRecord.objects.filter(
                student_profile=student,
                lesson_date=target_date,
                group=group,
            ).delete()
            deleted += n

    return Response({"deleted": deleted}, status=status.HTTP_200_OK)
