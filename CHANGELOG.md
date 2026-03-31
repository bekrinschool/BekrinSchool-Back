# Changelog

## [Unreleased]

### Changed

- **Database: PostgreSQL məcburi, SQLite fallback silindi**
  - `config/settings/base.py`: Artıq SQLite default yoxdur; DB konfiqurasiyası `config/settings/database.py`-dən gəlir.
  - `DATABASE_URL` təyin olunanda ondan istifadə olunur; yoxdursa `DB_NAME` + `DB_USER` (və əlavə olaraq `DB_PASSWORD`, `DB_HOST`, `DB_PORT`) ilə Postgres konfiqurasiyası qurulur.
  - Heç bir dəyər təyin olunmazsa `ImproperlyConfigured` atılır (sessiz SQLite fallback yoxdur).

### Added

- `config/settings/database.py`: Postgres-only DB config (get_database_config).
- `core/management/commands/verify_postgres.py`: 4 yoxlama (connection.vendor, SELECT 1, django_migrations, dbshell).
- `docs/POSTGRES_VERIFY.md`: Manual yoxlama addımları və troubleshooting.

### Deprecated / Commented (TODO: remove after confirmation)

- `config/settings/dev.py`: Köhnə SQLite override bloku comment-də qaldı; qeyd: "TODO: remove after confirmation — legacy SQLite override".

### Environment

- `.env`: `DATABASE_URL` aktiv (comment-dən çıxarıldı); izah Postgres-only üçün yeniləndi.
- `.env.example`: Postgres default dəyərləri və qısa izah (SQLite fallback yoxdur).
