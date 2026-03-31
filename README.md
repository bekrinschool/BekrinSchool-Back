# Bekrin School Backend

Django + DRF backend for Bekrin School course management system.

## Tech Stack

- Django 5.0.1
- Django REST Framework 3.14.0
- PostgreSQL (or SQLite for development)
- JWT Authentication (SimpleJWT)
- CORS support for Next.js frontend

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` and configure:

```env
DJANGO_SECRET_KEY=your-secret-key-here
DEBUG=1
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgresql://user:password@localhost:5432/bekrin_db
CORS_ALLOWED_ORIGINS=http://localhost:3000
CSRF_TRUSTED_ORIGINS=http://localhost:3000
```

### 3. Database Setup

**PostgreSQL (Recommended):**

```bash
# Create database
createdb bekrin_db

# Or using psql
psql -U postgres
CREATE DATABASE bekrin_db;
```

**SQLite (Development fallback):**

If `DATABASE_URL` is not set, SQLite will be used automatically.

### 4. Run Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Create Superuser

```bash
python manage.py createsuperuser
```

### 6. Seed Development Data

```bash
python manage.py seed_dev
```

This creates:
- 1 teacher (teacher@bekrinschool.az / teacher123)
- 3 students (student1@bekrinschool.az / student123)
- 1 parent (parent@bekrinschool.az / parent123)
- 2 groups
- Sample attendance, payments, test results

### 7. Run Development Server

```bash
python manage.py runserver
```

Server runs at: `http://localhost:8000`

## API Endpoints

### Authentication

- `POST /api/auth/login` - Login (email + password)
- `POST /api/auth/logout` - Logout
- `GET /api/auth/me` - Get current user

### Teacher Endpoints

- `GET /api/teacher/stats` - Dashboard statistics
- `GET /api/teacher/students?status=active|deleted` - List students
- `PATCH /api/teacher/students/{id}` - Update student
- `DELETE /api/teacher/students/{id}` - Soft delete student
- `DELETE /api/teacher/students/{id}/hard` - Hard delete student
- `GET /api/teacher/groups` - List groups
- `POST /api/teacher/groups` - Create group
- `PATCH /api/teacher/groups/{id}` - Update group
- `DELETE /api/teacher/groups/{id}` - Delete group
- `POST /api/teacher/groups/{id}/students` - Add students to group
- `DELETE /api/teacher/groups/{id}/students/{studentId}` - Remove student from group
- `POST /api/teacher/groups/move-student` - Move student between groups
- `GET /api/teacher/payments?groupId=&studentId=` - List payments
- `POST /api/teacher/payments` - Create payment
- `DELETE /api/teacher/payments/{id}` - Delete payment

### Student Endpoints

- `GET /api/student/attendance` - Get attendance records
- `GET /api/student/results` - Get test results
- `GET /api/student/coding` - Get coding exercises

### Parent Endpoints

- `GET /api/parent/children` - Get children list with stats
- `GET /api/parent/attendance?studentId=` - Get child's attendance
- `GET /api/parent/payments?studentId=` - Get child's payments

## API Documentation

- Swagger UI: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`
- Schema: `http://localhost:8000/api/schema/`

## Project Structure

```
bekrin-back/
├── config/              # Django project settings
│   ├── settings/
│   │   ├── base.py      # Base settings
│   │   ├── dev.py       # Development settings
│   │   └── prod.py      # Production settings
│   └── urls.py          # Root URLconf
├── accounts/            # Custom User + Auth
├── students/            # Student profiles + Parent-Child
├── groups/              # Groups + Group-Student
├── attendance/          # Attendance records
├── payments/            # Payment records
├── tests/               # Test results (placeholder)
├── coding/              # Coding exercises (placeholder)
└── manage.py
```

## Models Overview

### User (Custom)
- Email-based authentication
- Roles: teacher, student, parent
- No username field

### StudentProfile
- OneToOne with User (role=student)
- grade, phone, balance, status

### ParentChild
- Links parent user to student profile
- Many-to-Many relationship

### Group
- name, is_active, order
- created_by (teacher)

### GroupStudent
- Links group to student
- active flag, joined_at timestamp

### AttendanceRecord
- Daily attendance per student per group
- status: present, absent, late, excused

### Payment
- Student payment records
- amount, date, method, status
- Auto-generated receipt_no

### TestResult (Placeholder)
- Student test scores
- TODO: Expand with full test management

### CodingExercise (Placeholder)
- Coding exercise definitions
- TODO: Expand with full coding management

## Permissions

- Teacher endpoints: `IsTeacher` permission
- Student endpoints: `IsStudent` permission
- Parent endpoints: `IsParent` permission
- Parent can only access their own children's data

## Notes

- No signup endpoint - users created by admin/teacher only
- JWT tokens returned in response body (TODO: httpOnly cookie support)
- All API responses in English (UI is Azerbaijani)
- Frontend expects specific response formats (see frontend code)

## PDF to Image Conversion (Exam Viewer)

The backend converts exam PDFs into per-page images under:

- `MEDIA_ROOT/exam_pages/run_<run_id>/page_001.jpg`, ...

Converter priority:

- **PyMuPDF** (`fitz`) is used **first**. It works on all platforms without extra system installs.
- **pdf2image** is used only as a fallback. It requires **Poppler**:
  - **Linux**: `apt install poppler-utils`
  - **macOS**: `brew install poppler`
  - **Windows**: install Poppler and add `Library/bin` to `PATH` or set `POPPLER_PATH`

To confirm which converter is active in production, check backend logs for `pdf_convert fitz_ok` vs `pdf_convert pdf2image_ok`.

## Development

```bash
# Run checks
python manage.py check

# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Run tests (when added)
python manage.py test
```

## Production Deployment

1. Set `DEBUG=False` in `.env`
2. Configure `ALLOWED_HOSTS`
3. Set up PostgreSQL database
4. Configure static files serving
5. Set up HTTPS
6. Enable CSRF cookie secure flag
7. Set secure JWT cookie settings
