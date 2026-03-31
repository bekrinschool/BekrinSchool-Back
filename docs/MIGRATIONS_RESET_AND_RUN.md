# Migrations reset and run (təmiz başlanğıc)

Yeni schema (ERD) üçün migration-lar artıq yaradılıb. Köhnə verilənlər bazası ilə uyğunsuzluq olarsa aşağıdakı addımları yerinə yetirin.

## 1. Verilənlər bazasını sıfırlayın

### SQLite
- Layihə qovluğundakı `db.sqlite3` faylını silin.

### PostgreSQL
- Ya mövcud DB-ni silib yenidən yaradın:
  ```bash
  psql -U postgres -c "DROP DATABASE IF EXISTS bekrin_db;"
  psql -U postgres -c "CREATE DATABASE bekrin_db ENCODING 'UTF8';"
  ```
- Ya da `.env`-də başqa bir DB adı istifadə edin.

## 2. Migrate edin

```bash
cd bekrin-back
.venv\Scripts\activate
python manage.py migrate
```

Sıra: core → accounts → students → groups → attendance → payments → coding → tests (Django özü sıralayır).

## 3. Seed data

```bash
python manage.py seed_dev
```

## 4. (İstəyə bağlı) Superuser

```bash
python manage.py createsuperuser
```

## Qeyd

Əgər `InconsistentMigrationHistory` xətası alarsanız, səbəb köhnə DB-dir. Yuxarıdakı 1-ci addımı (DB-ni silmək/sıfırlamaq) etməlisiniz.
