# Bekrin School — Database Integrity Audit Report

## Phase 1: Invariants (Defined)

### I1) User role implies profile exists
- `User(role=student)` => `StudentProfile` exists
- `User(role=parent)` => `ParentProfile` exists
- `User(role=teacher)` => `TeacherProfile` exists
- Enforced by: `sync_profiles` management command, signals on role change

### I2) Organization scoping
- Teacher, Student, Parent belong to one organization (no NULL in production)
- Group, GroupStudent, Payment, Attendance belong to organization
- Enforced by: `filter_by_organization()`, `belongs_to_user_organization()`, `sync_integrity --apply`

### I3) Single source of truth for group membership
- **GroupStudent** table only; do not infer from other places
- Active membership: `active=True` AND `left_at__isnull=True`
- Canonical helpers: `get_active_students_for_group(group)`, `get_active_groups_for_student(student_profile)`

### I4) Create/update API returns data matching list/detail
- All POST/PATCH must return full serialized object
- Visible immediately after create (no caching mismatch)

---

## Phase 2: Entity Audit

### Student
| Attribute | Source of truth | Endpoints using it |
|-----------|-----------------|-------------------|
| StudentProfile.id | students_studentprofile.id | Teacher students, groups/{id}/students, payments (studentId) |
| StudentProfile.user_id | accounts_user.id | All profile lookups |
| Student list | StudentProfile + filter_by_organization(user__organization) | GET /teacher/students |

### Group membership
| Attribute | Source of truth | Endpoints |
|-----------|-----------------|-----------|
| Active members | GroupStudent(active=True, left_at__isnull=True) | GET /teacher/groups/{id}/students |
| Add student | GroupStudent.get_or_create | POST /teacher/groups/{id}/students |
| Remove | GroupStudent.active=False | DELETE /teacher/groups/{id}/students/{id} |

### Payment
| Attribute | Source of truth | Endpoints |
|-----------|-----------------|-----------|
| Payment | payments table, student_profile FK | Teacher: GET/POST/DELETE /teacher/payments |
| Teacher list | filter_by_organization(Payment.organization) + optional groupId/studentId | GET /teacher/payments |
| Parent list | ParentChild check + student_profile_id filter | GET /parent/payments?studentId= |
| Student | N/A (no dedicated student payments endpoint; parent sees for child) | — |

### Attendance
| Attribute | Source of truth | Endpoints |
|-----------|-----------------|-----------|
| AttendanceRecord | attendance_records, student_profile FK | Teacher attendance, Parent attendance |

---

## Root Causes Found

1. **Payment create broken (CRITICAL)**: Teacher payment POST view transformed `studentId` → `student_profile` and `groupId` → `group` before passing to serializer. PaymentCreateSerializer expects `studentId` and `groupId`. Validation failed, payment never created.
2. **Payment DELETE**: Did hard delete; now uses soft delete (deleted_at). No org/ownership check — fixed.
3. **groupId empty string**: Frontend sends `groupId: ""` for optional group; IntegerField rejected it. Fixed with `_NullableIntegerField`.
4. **Organization null**: Payment/Attendance/GroupStudent can have organization=NULL; teacher filter excludes them. `sync_integrity --apply` fixes.

---

## Changes Made

| File | Change |
|------|--------|
| groups/views/teacher.py | Removed broken data transform in POST; pass request.data as-is. DELETE: soft delete + org check. |
| payments/serializers.py | _NullableIntegerField for groupId; org validation in create(). |
| core/management/commands/sync_integrity.py | New command: profiles, users org, Payment/Attendance/GroupStudent org sync. |
| payments/migrations/0002_add_organization_index.py | Index on Payment.organization. |

---

## Verification Checklist

- [ ] `python manage.py check`
- [ ] `python manage.py migrate`
- [ ] `python manage.py sync_integrity` (dry-run)
- [ ] `python manage.py sync_integrity --apply` (if safe)
- [ ] Create student, parent, group, add student to group
- [ ] Create payment for student, confirm
- [ ] Teacher payments list shows it
- [ ] Parent payments (child) shows it
- [ ] Restart server, repeat — must persist
