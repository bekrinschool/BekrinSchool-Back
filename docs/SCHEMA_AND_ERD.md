# Bekrin School — PostgreSQL Schema & ERD

## 1) ERD — MƏTN FORMATINDA (UTF-8, column adları ingiliscə)

### ENUM-lar / sabitlər
- **user_role:** teacher | student | parent | admin (optional)
- **attendance_status:** present | late | absent | excused
- **payment_method:** cash | card | bank | other
- **payment_status:** paid | unpaid | partial | cancelled
- **task_difficulty:** easy | medium | hard
- **test_type:** quiz | exam
- **question_type:** mcq | numeric | written
- **submission_status:** passed | failed | error | timeout

---

### 1) organization
| Column       | Type        | Notes                    |
|-------------|-------------|--------------------------|
| id          | PK          |                          |
| name        | varchar     |                          |
| slug        | varchar     | unique                   |
| created_at  | timestamp   |                          |
| updated_at  | timestamp   |                          |

---

### 2) users + profillər

**user**
| Column         | Type        | Notes                          |
|----------------|-------------|--------------------------------|
| id             | PK          |                                |
| organization_id| FK → organization | nullable (single-tenant) |
| email          | varchar     | unique, indexed                |
| password_hash  | varchar     | Django auth                    |
| full_name      | varchar     | AZ content OK                  |
| phone          | varchar     | nullable                       |
| role           | user_role   | indexed                        |
| is_active      | bool        |                                |
| last_login     | timestamp   | nullable (Django default)      |
| created_at     | timestamp   | date_joined                    |
| updated_at     | timestamp   |                                |

**student_profile** (1-1 User, role=student)
| Column     | Type     | Notes        |
|------------|----------|-------------|
| user_id    | PK, FK   | → user.id   |
| grade      | varchar  | "10", "5A"  |
| balance    | decimal  |              |
| deleted_at | timestamp| nullable, soft delete |
| notes      | text     | nullable     |
| created_at | timestamp|              |
| updated_at | timestamp|              |

**parent_profile** (1-1 User, role=parent)
| Column     | Type     | Notes        |
|------------|----------|-------------|
| user_id    | PK, FK   | → user.id   |
| deleted_at | timestamp| nullable     |
| created_at | timestamp|              |
| updated_at | timestamp|              |

**teacher_profile** (1-1 User, role=teacher)
| Column        | Type     | Notes        |
|---------------|----------|-------------|
| user_id       | PK, FK   | → user.id   |
| display_title | varchar  | nullable     |
| created_at    | timestamp|              |
| updated_at    | timestamp|              |

**parent_student** (valideyn–şagird əlaqəsi)
| Column   | Type     | Notes                    |
|----------|----------|--------------------------|
| id       | PK       |                          |
| organization_id | FK | nullable                |
| parent_id| FK → user| role=parent              |
| student_id| FK → user| role=student             |
| relation | varchar  | nullable (ana/ata)       |
| created_at| timestamp|                         |
| UNIQUE(parent_id, student_id) | | |

---

### 3) groups + schedule + membership

**group**
| Column        | Type     | Notes                          |
|---------------|----------|--------------------------------|
| id            | PK       |                                |
| organization_id | FK     | nullable                       |
| teacher_id    | FK → user| created_by                     |
| code          | varchar  | nullable, "Qrup1"              |
| name          | varchar  | display / manual name           |
| days_of_week  | int[]    | PostgreSQL array [1,2,3,4]     |
| start_time    | time     | nullable                       |
| display_name  | varchar  | auto: "Qrup1: 1-4 11:00"       |
| is_active     | bool     |                                |
| sort_order    | int      | nullable                       |
| deleted_at    | timestamp| nullable                       |
| created_at    | timestamp|                                |
| updated_at    | timestamp|                                |

**group_membership**
| Column      | Type     | Notes                    |
|-------------|----------|--------------------------|
| id          | PK       |                          |
| organization_id | FK   | nullable                 |
| group_id    | FK       | indexed                  |
| student_id  | FK → user| role=student, indexed     |
| joined_at   | timestamp|                          |
| left_at     | timestamp| nullable                 |
| is_active   | bool     |                          |
| UNIQUE(group_id, student_id) | |                      |

---

### 4) attendance

**attendance_record**
| Column      | Type     | Notes                    |
|-------------|----------|--------------------------|
| id          | PK       |                          |
| organization_id | FK   | nullable                 |
| group_id    | FK       | indexed                  |
| student_id   | FK → user| indexed                  |
| lesson_date  | date     | indexed                  |
| status      | attendance_status |       |
| marked_by_id | FK → user| nullable                 |
| marked_at   | timestamp| nullable                 |
| note        | text     | nullable                 |
| created_at  | timestamp|                          |
| updated_at  | timestamp|                          |
| UNIQUE(group_id, student_id, lesson_date) | |                |

---

### 5) payments

**payment**
| Column        | Type     | Notes        |
|---------------|----------|-------------|
| id            | PK       |             |
| organization_id | FK     | nullable    |
| student_id    | FK → user| indexed     |
| group_id      | FK       | nullable    |
| payment_date  | date     | indexed     |
| title         | varchar  | nullable    |
| amount        | decimal  |             |
| method        | payment_method |     |
| status        | payment_status |     |
| note          | text     | nullable    |
| receipt_no    | varchar  | unique      |
| created_by_id | FK → user|             |
| created_at    | timestamp|             |
| updated_at    | timestamp|             |
| deleted_at    | timestamp| nullable    |

---

### 6) coding

**coding_topic**
| Column   | Type     | Notes        |
|----------|----------|-------------|
| id       | PK       |             |
| organization_id | FK | nullable    |
| name     | varchar  | unique per org |
| created_at | timestamp|           |

**coding_task**
| Column       | Type     | Notes        |
|--------------|----------|-------------|
| id           | PK       |             |
| organization_id | FK    | nullable    |
| topic_id     | FK       | nullable    |
| topic_name   | varchar  | nullable (legacy) |
| title        | varchar  |             |
| description  | text     |             |
| starter_code | text     |             |
| difficulty   | task_difficulty |  |
| points       | int      | nullable    |
| order_index  | int      | nullable    |
| is_active    | bool     |             |
| deleted_at   | timestamp| nullable    |
| created_by_id| FK → user|             |
| created_at   | timestamp|             |
| updated_at   | timestamp|             |

**coding_test_case**
| Column     | Type     | Notes        |
|------------|----------|-------------|
| id         | PK       |             |
| task_id    | FK       | indexed     |
| input      | text     |             |
| expected   | text     |             |
| explanation| text     | nullable    |
| order_index| int      | nullable    |
| created_at | timestamp|             |

**coding_submission**
| Column        | Type     | Notes        |
|---------------|----------|-------------|
| id            | PK       |             |
| organization_id | FK    | nullable    |
| task_id       | FK       | indexed     |
| student_id    | FK → user| indexed     |
| submitted_code| text     |             |
| status        | submission_status |  |
| passed_count  | int      | nullable    |
| failed_count  | int      | nullable    |
| error_message | text     | nullable    |
| runtime_ms    | int      | nullable    |
| attempt_no    | int      | nullable    |
| created_at    | timestamp| indexed      |

---

### 7) tests

**test**
| Column        | Type     | Notes        |
|---------------|----------|-------------|
| id            | PK       |             |
| organization_id | FK    | nullable    |
| created_by_id | FK → user|             |
| type          | test_type|             |
| title         | varchar  |             |
| pdf_url       | text     |             |
| config        | jsonb    | nullable    |
| is_active     | bool     |             |
| deleted_at    | timestamp| nullable    |
| created_at    | timestamp|             |
| updated_at    | timestamp|             |

**test_answer_key**
| Column             | Type   | Notes   |
|--------------------|--------|--------|
| id                 | PK     |        |
| test_id            | FK     | unique |
| mcq_answers        | jsonb  | {"1":"A"} |
| numeric_answers    | jsonb  | {"11":"25"} |
| written_instructions | text | nullable |
| created_at         | timestamp |     |
| updated_at         | timestamp |     |

**test_assignment**
| Column        | Type     | Notes        |
|---------------|----------|-------------|
| id            | PK       |             |
| organization_id | FK    | nullable    |
| test_id       | FK       | indexed     |
| group_id      | FK       | nullable    |
| student_id    | FK → user| nullable    |
| available_from| timestamp| nullable    |
| available_to  | timestamp| nullable    |
| created_at    | timestamp|             |
| CHECK: group_id OR student_id at least one | | |

**test_attempt**
| Column      | Type     | Notes        |
|-------------|----------|-------------|
| id          | PK       |             |
| organization_id | FK   | nullable    |
| test_id     | FK       | indexed     |
| student_id  | FK → user| indexed     |
| started_at  | timestamp|             |
| submitted_at| timestamp| nullable    |
| answers     | jsonb    |             |
| score       | decimal  | nullable    |
| status      | varchar  | started/submitted/graded |
| created_at  | timestamp|             |

---

## 2) Why this design

1. **Organization from day one** — Single tenant now; multi-tenant later without schema break. All major tables have optional `organization_id`.
2. **Soft delete** — `deleted_at` on student_profile, group, payment, coding_task, test keeps history and allows “deleted” tabs in UI.
3. **Schedule as source of truth** — `days_of_week` + `start_time` on group; `display_name` can be cached/auto-generated so UI shows "Qrup1: 1-4 11:00".
4. **Attendance grid** — One row per (group, student, lesson_date); `marked_by` / `marked_at` for audit; indexes on (group_id, lesson_date) and (student_id, lesson_date) for fast month/range queries.
5. **Coding & tests** — Separate topic/task/test_case and test/answer_key/assignment/attempt allow bulk import (JSON), ranking from submissions, and future anti-cheat via `config` jsonb.
6. **Legacy parity** — Same flows (groups, attendance, payments, bulk import, coding monitoring, test create) without renaming columns to Azerbaijani; content (full_name, title, description) stays UTF-8 AZ.

---

## 3) Migrations reset addımları

1. Bütün app migration fayllarını sil (yalnız `__init__.py` saxlanılır):
   - `accounts/migrations/0001_initial.py` (və digər 00xx_*.py)
   - `students/migrations/...`
   - `groups/migrations/...`
   - `attendance/migrations/...`
   - `payments/migrations/...`
   - `coding/migrations/...`
   - `tests/migrations/...`
   - `core/migrations/...` (yeni)
2. PostgreSQL: DB-ni drop edib yenidən yarat və ya SQLite üçün `db.sqlite3` sil.
3. Sıra ilə:
   ```bash
   python manage.py makemigrations core
   python manage.py makemigrations accounts
   python manage.py makemigrations students
   python manage.py makemigrations groups
   python manage.py makemigrations attendance
   python manage.py makemigrations payments
   python manage.py makemigrations coding
   python manage.py makemigrations tests
   python manage.py migrate
   ```
4. Seed:
   ```bash
   python manage.py seed_dev
   ```

---

## 4) Seed run addımları

- `python manage.py seed_dev` — 1 teacher, 1 parent, 2–3 student, 2 group, sample attendance, payments, coding tasks + test cases + submissions, sample test + answer key.
- Parollar `set_password` ilə hash-lənir; AZ mətnlər (ad, qrup adı) problemsiz saxlanılır (UTF-8).

---

## 5) Frontend inteqrasiya üçün endpoint mapping

| Frontend ehtiyacı              | Endpoint (minimal) |
|--------------------------------|---------------------|
| Auth                           | POST /api/auth/login, GET /api/auth/me |
| Groups list/create/update/delete | GET/POST/PATCH/DELETE /api/teacher/groups |
| Group members add/remove      | POST/DELETE /api/teacher/groups/{id}/students |
| Students active/deleted       | GET /api/teacher/students?status=active\|deleted |
| Soft delete / hard delete     | DELETE /api/teacher/students/{id}, DELETE .../hard |
| Attendance grid (month/range)  | GET /api/teacher/attendance?group_id=&month= |
| Attendance cell update         | PATCH/PUT /api/teacher/attendance (atomic) |
| Payments list (filter)         | GET /api/teacher/payments?groupId=&studentId= |
| Payments create/delete        | POST /api/teacher/payments, DELETE .../{id} |
| Bulk import users (CSV)       | POST /api/teacher/bulk-import/users |
| Bulk import coding tasks (JSON)| POST /api/teacher/coding/import |
| Coding tasks CRUD             | GET/POST/PATCH/DELETE /api/teacher/coding/tasks |
| Submissions per student       | GET /api/teacher/coding/submissions?studentId= |
| Ranking summary               | GET /api/teacher/coding/ranking |
| Tests list/create             | GET/POST /api/teacher/tests |
| Test answer key save          | POST/PUT /api/teacher/tests/{id}/answer-key |
| Student/Parent dashboard      | GET /api/student/attendance, GET /api/parent/children (summary) |

Bu sənəd "single source of truth" kimi schema və addımları təyin edir; implementasiya bu ERD-ə uyğun qurulur.

---

## 6) Final data model summary (Django modelləri)

| Table / Model | Əsas sahələr | Əlaqələr |
|---------------|--------------|----------|
| **Organization** | id, name, slug | — |
| **User** | id, organization_id, email, full_name, phone, role, is_active, date_joined, updated_at | → Organization |
| **StudentProfile** | user_id (PK), grade, balance, notes, deleted_at | 1-1 User |
| **ParentProfile** | user_id (PK), deleted_at | 1-1 User |
| **TeacherProfile** | user_id (PK), display_title | 1-1 User |
| **ParentChild** | id, parent_id, student_id, relation | → User (parent), → User (student) |
| **Group** | id, organization_id, teacher_id, code, name, days_of_week, start_time, display_name, is_active, sort_order, deleted_at | → Organization, → User |
| **GroupStudent** | id, group_id, student_profile_id, joined_at, left_at, active | → Group, → StudentProfile |
| **AttendanceRecord** | id, group_id, student_profile_id, lesson_date, status, marked_by_id, marked_at | → Group, → StudentProfile, → User |
| **Payment** | id, student_profile_id, group_id, payment_date, title, amount, method, status, receipt_no, created_by_id, deleted_at | → StudentProfile, → Group, → User |
| **CodingTopic** | id, organization_id, name | → Organization |
| **CodingTask** | id, topic_id, topic_name, title, description, starter_code, difficulty, points, order_index, is_active, deleted_at, created_by_id | → CodingTopic, → User |
| **CodingTestCase** | id, task_id, input_data, expected, explanation, order_index | → CodingTask |
| **CodingSubmission** | id, task_id, student_id (User), submitted_code, status, passed_count, failed_count, attempt_no, created_at | → CodingTask, → User |
| **CodingProgress** | student_profile_id, exercise_id (CodingTask), status, score | → StudentProfile, → CodingTask |
| **Test** | id, organization_id, created_by_id, type, title, pdf_url, config (JSON), is_active, deleted_at | → User |
| **TestAnswerKey** | id, test_id (1-1), mcq_answers, numeric_answers, written_instructions | → Test |
| **TestAssignment** | id, test_id, group_id, student_id, available_from, available_to | → Test, → Group, → User |
| **TestAttempt** | id, test_id, student_id, started_at, submitted_at, answers (JSON), score, status | → Test, → User |
| **TestResult** | student_profile_id, group_id, test_name, score, max_score, date | → StudentProfile, → Group (legacy/summary) |
