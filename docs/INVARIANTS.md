# Bekrin School — Database Invariants

These invariants MUST hold in production. Code must not violate them.

## I1) User role implies profile exists

- `User(role=student)` => `StudentProfile` exists
- `User(role=parent)` => `ParentProfile` exists
- `User(role=teacher)` => `TeacherProfile` exists

**Enforced by:** `sync_profiles` command, signals in `students/signals.py`

## I2) Organization scoping

- Teacher, Student, Parent belong to exactly one organization (no NULL org in production)
- Group belongs to organization
- GroupStudent links group + student_profile within same org
- Payment belongs to organization AND student_profile
- Attendance belongs to organization AND student_profile AND (optionally) group
- Exam assignments use group membership from GroupStudent

**Enforced by:** `filter_by_organization()`, `belongs_to_user_organization()`, `sync_integrity --apply`

## I3) Single source of truth for group membership

- Use **GroupStudent** table only (do not infer from other places)
- Active membership: `active=True` AND `left_at__isnull=True`
- Canonical helpers: `get_active_students_for_group(group)`, `get_active_groups_for_student(student_profile)` in `groups/services.py`

## I4) API consistency

- Every create/update API MUST return data that matches list/detail endpoints
- Data MUST be visible immediately after create (no stale cache)
