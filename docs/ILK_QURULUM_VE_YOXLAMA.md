# İlk qurulum və yoxlama (bekrin-back)

PostgreSQL **port 5433**-də işləyir (Windows default 5432 məşğul olanda). `.env`-də **postgres** / **bekrin123** və port **5433** istifadə olunur.

## 1. Postgres hazırlığı

- Server **5433**-də işləməlidir.
- Default **postgres** verilənlər bazası və **postgres** superuser (parol: **bekrin123**) ilə dərhal işləyə bilərsiniz; əlavə DB yaratmağa ehtiyac yoxdur.
- İstəsəniz **bekrin_db** və **bekrin_user** yaratmaq üçün: `docs/postgres_setup.sql` (port 5433 ilə qoşulun: `psql -h localhost -p 5433 -U postgres -f docs/postgres_setup.sql`).

## 2. Backend addımları (bekrin-back qovluğunda)

```powershell
cd bekrin-school1\bekrin-back
.venv\Scripts\Activate.ps1
python manage.py makemigrations
python manage.py migrate
python manage.py seed_dev
python manage.py runserver
```

## 3. Yoxlama

- `python manage.py runserver` — konsolda **`[startup] DB=postgresql`**.
- `python manage.py shell` → `from django.db import connection` → `print(connection.vendor)` → **postgresql**; `print(connection.settings_dict["PORT"])` → **5433**.
- Brauzer: http://127.0.0.1:8000/admin/ — **teacher@bekrinschool.az** / **teacher123**.

Ətraflı: **docs/POSTGRES_VERIFY.md** (manual checklist, problemlər).
