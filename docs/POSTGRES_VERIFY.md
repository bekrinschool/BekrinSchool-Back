# PostgreSQL — Yoxlama və problem həlli (bekrin-back)

## 1. .env oxunurmu?

- **config/settings/base.py**: `environ.Env.read_env(BASE_DIR / '.env')` — `.env` faylı **bekrin-back** qovluğundadır (manage.py ilə eyni səviyyə).
- **DATABASES**: `config/settings/database.py` → `get_database_config(env)` — `DATABASE_URL` və ya `DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT` ilə idarə olunur. Heç bir silent SQLite fallback yoxdur.

---

## 2. Manual verification (özün yoxlama)

Aşağıdakıları **bekrin-back** qovluğunda, venv aktiv iken işlədin.

| # | Komanda / addım | Gözlənilən nəticə |
|---|------------------|-------------------|
| 1 | `pg_isready -h localhost -p 5433` | `accepting connections` (psql PATH-də olmalıdır) |
| 2 | `psql -h localhost -p 5433 -U postgres` | psql açılır; parol: bekrin123. Sonra `\q` ilə çıx. |
| 3 | `python manage.py shell` → aşağıdakılar | |
|    | `from django.db import connection` | |
|    | `print(connection.vendor)` | **postgresql** |
|    | `print(connection.settings_dict["PORT"])` | **5433** (və ya `'5433'`) |
|    | `from django.db import connection; c = connection.cursor(); c.execute('SELECT 1'); print(c.fetchone())` | **(1,)** — real əlaqə. |
| 4 | `python manage.py verify_postgres` | Ən azı 3/4 check keçməlidir. |
| 5 | `python manage.py migrate` | Xətə olmadan bitməli (və ya "No migrations to apply."). |
| 6 | `python manage.py runserver` | Konsolda **`[startup] DB=postgresql`** görünməlidir. |
| 7 | (psql PATH-dədirsə) `python manage.py dbshell` | **psql** açılmalıdır (port 5433 ilə). |

---

## 3. DB yaratma (bekrin_db / bekrin_user — istəyə bağlı)

İndi default **postgres** DB və **postgres** istifadəçi ilə işləyirsiniz. Ayrıca **bekrin_db** və **bekrin_user** istəyirsinizsə:

**Port 5433 ilə qoşulun (psql):**
```bash
psql -h localhost -p 5433 -U postgres
```
Parol: **bekrin123**

**Query Tool / psql-də:**
```sql
CREATE ROLE bekrin_user WITH LOGIN PASSWORD 'bekrin_pass';
CREATE DATABASE bekrin_db OWNER bekrin_user;
GRANT ALL PRIVILEGES ON DATABASE bekrin_db TO bekrin_user;
```

Sonra **.env**-də:
```
DATABASE_URL=postgresql://bekrin_user:bekrin_pass@localhost:5433/bekrin_db
```

Yenidən `python manage.py migrate` işlədin.

---

## 4. Ən çox rast gəlinən problemlər + həll

| Problem | Səbəb / həll |
|--------|----------------|
| **Wrong port / connection refused** | Postgres 5433-də işləyir, 5432 yox. `.env`-də `DATABASE_URL`-də port **5433** olmalıdır; və ya `DB_PORT=5433`. |
| **password authentication failed for user "postgres"** | Parol `.env`-də **bekrin123** olmalıdır (quraşdırmada qoyduğunuz). Faylda boşluq/typo yoxlayın. |
| **.env oxunmur / DEBUG/DB dəyişmir** | `.env` faylı **bekrin-back** (manage.py ilə eyni qovluqda) olmalıdır. Path: `config/settings/base.py`-də `BASE_DIR / '.env'`. |
| **psql / pg_isready tapılmır (PATH)** | Windows-da Postgres **bin** qovluğunu PATH-ə əlavə edin (məs. `C:\Program Files\PostgreSQL\16\bin`). Və ya yalnız Django ilə yoxlayın: `python manage.py shell` → `connection.vendor` və `connection.settings_dict["PORT"]`. |
| **ImproperlyConfigured: PostgreSQL is required** | `DATABASE_URL` və ya `DB_NAME`/`DB_USER` təyin olunmayıb. `.env`-də ən azı `DATABASE_URL=postgresql://postgres:bekrin123@localhost:5433/postgres` olmalıdır. |
| **relation "django_migrations" does not exist** | `python manage.py migrate` işlədin. |

---

## 5. Qısa command checklist (kopyala-ışlat)

```powershell
cd bekrin-school1\bekrin-back
.venv\Scripts\Activate.ps1
pg_isready -h localhost -p 5433
psql -h localhost -p 5433 -U postgres -c "SELECT 1"
python manage.py shell -c "from django.db import connection; print('vendor:', connection.vendor, 'port:', connection.settings_dict.get('PORT'))"
python manage.py migrate
python manage.py runserver
```

Gözlənilən: vendor **postgresql**, port **5433**, migrate uğurlu, runserver-də `[startup] DB=postgresql`.
