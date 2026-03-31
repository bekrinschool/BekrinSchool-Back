-- Optional: create dedicated DB and user (when you want to switch from default 'postgres' DB).
-- Run as superuser. Windows: Postgres often on port 5433.
-- psql: psql -h localhost -p 5433 -U postgres -f docs/postgres_setup.sql
-- pgAdmin: connect to server (port 5433), Query Tool, paste and run.

CREATE ROLE bekrin_user WITH LOGIN PASSWORD 'bekrin_pass';
CREATE DATABASE bekrin_db OWNER bekrin_user;
GRANT ALL PRIVILEGES ON DATABASE bekrin_db TO bekrin_user;

-- Then in .env set: DATABASE_URL=postgresql://bekrin_user:bekrin_pass@localhost:5433/bekrin_db
