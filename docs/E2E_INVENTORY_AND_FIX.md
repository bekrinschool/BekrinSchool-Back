# E2E Inventory & Fix Report

## Part 1 — Inventory (Findings)

### No separate e2e_* tables or models exist

Search results for `E2EUser`, `E2EGroup`, `e2e_user`, `e2e_group` return **no matches**.

- **seed_e2e** (`tests/management/commands/seed_e2e.py`): Uses **canonical** models:
  - `User` (emails: teacher_e2e@..., student_e2e_1@..., parent_e2e_1@...)
  - `Group`, `GroupStudent`, `StudentProfile`, `ParentChild`, `TeacherProfile`
  - `Exam`, `ExamAssignment`, `ExamQuestion`, `TeacherPDF`, `CodingTask`, etc.
- **seed_dev** (`students/management/commands/seed_dev.py`): Same canonical models:
  - Org `bekrin-default`, teacher@bekrinschool.az, student1@..., etc.

### Root cause of “Teacher sees no users / only e2e data”

- **Organization scoping**: `/api/users/` filters by `request.user.organization`
  - Teacher with org A sees only users in org A
  - Teacher with org B sees only users in org B
  - Teacher with `organization=NULL` sees only users with `organization__isnull=True` (often none)
- **Two orgs**: seed_dev → `bekrin-default`, seed_e2e → `bekrin-e2e`
- Teacher from seed_dev does not see seed_e2e users and vice versa

### Canonical vs “e2e” mapping (conceptual)

| Canonical model      | “e2e” equivalent | Notes                                           |
|----------------------|------------------|-------------------------------------------------|
| User                 | Same             | Emails like teacher_e2e@, student_e2e_1@       |
| StudentProfile       | Same             | -                                              |
| Group                | Same             | -                                              |
| GroupStudent         | Same             | -                                              |
| All other models     | Same             | No duplicate tables                            |

### Endpoints using org scoping

- `GET /api/users/` — `_users_queryset` filters by `organization=request.user.organization`
- `GET /api/teacher/students` — `filter_by_organization(qs, request.user, 'user__organization')`
- `GET /api/teacher/groups` — `filter_by_organization(groups, request.user)`
- `GET /api/teacher/payments` — `filter_by_organization(payments, request.user)`
- `filter_by_organization`: when user has org, filters queryset by that org; when user has no org, returns all (single-tenant)

## Part 2 — Fix: Single-tenant (Teacher sees all)

Implemented:
- Added `SINGLE_TENANT` setting (default True) in `config/settings/base.py`
- `accounts/views/users.py`: _users_queryset returns ALL users when SINGLE_TENANT; relaxed org check in update/soft_delete/restore
- `core/utils.py`: filter_by_organization returns queryset unfiltered when SINGLE_TENANT (affects teacher students, groups, payments)
- Teacher PDFs: list shows all PDFs when SINGLE_TENANT
- Teacher exam attempts/detail: any teacher can view any exam's attempts when SINGLE_TENANT
- Coding monitor: no org filter when SINGLE_TENANT; added search param; last_activity sort; student submissions paginated

## Part 3 — Data merge (N/A)

No e2e_* tables exist. All data uses canonical User, Group, etc. No migration needed.

## Part 4 — Part 6 — Validation

Backend: python manage.py check, migrate - OK
seed_dev: fails with UnicodeEncodeError on Windows console (pre-existing; Azerbaijani chars in org name)
