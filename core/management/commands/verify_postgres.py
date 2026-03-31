"""
Verify that Django uses PostgreSQL (4 checks).
Usage: python manage.py verify_postgres
"""
import os
import subprocess
import sys
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Verify DB is PostgreSQL: vendor, SELECT 1, django_migrations, dbshell (psql).'

    def handle(self, *args, **options):
        ok = 0
        # 1) connection.vendor
        try:
            vendor = connection.vendor
            if vendor == 'postgresql':
                self.stdout.write(self.style.SUCCESS('1. connection.vendor: postgresql'))
                ok += 1
            else:
                self.stdout.write(self.style.ERROR(f'1. connection.vendor: {vendor!r} (expected postgresql)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'1. connection.vendor failed: {e}'))

        # 2) SELECT 1
        try:
            with connection.cursor() as cur:
                cur.execute('SELECT 1')
                row = cur.fetchone()
            if row and row[0] == 1:
                self.stdout.write(self.style.SUCCESS('2. SELECT 1: OK'))
                ok += 1
            else:
                self.stdout.write(self.style.ERROR('2. SELECT 1: unexpected result'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'2. SELECT 1 failed: {e}'))

        # 3) django_migrations table
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'django_migrations'"
                )
                exists = cur.fetchone() is not None
            if exists:
                self.stdout.write(self.style.SUCCESS('3. django_migrations table: exists'))
                ok += 1
            else:
                self.stdout.write(self.style.WARNING('3. django_migrations table: not found (run migrate first)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'3. django_migrations check failed: {e}'))

        # 4) dbshell (psql) — try running dbshell with \q
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            result = subprocess.run(
                [sys.executable, 'manage.py', 'dbshell'],
                input=b'\\q\n',
                capture_output=True,
                timeout=5,
                cwd=project_root,
            )
            # If dbshell opens psql, \q exits 0. If it's sqlite, we might get different behavior.
            if result.returncode == 0:
                self.stdout.write(self.style.SUCCESS('4. dbshell: exited 0 (psql expected)'))
                ok += 1
            else:
                self.stdout.write(
                    self.style.WARNING(f'4. dbshell: exit code {result.returncode} (psql may not be in PATH on Windows)')
                )
        except FileNotFoundError:
            self.stdout.write(self.style.WARNING('4. dbshell: manage.py not in cwd or psql not in PATH'))
        except subprocess.TimeoutExpired:
            self.stdout.write(self.style.WARNING('4. dbshell: timed out'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'4. dbshell: {e}'))

        if ok >= 3:
            self.stdout.write(self.style.SUCCESS(f'Postgres verification: {ok}/4 checks passed.'))
        else:
            self.stdout.write(self.style.ERROR(f'Postgres verification: {ok}/4 checks passed. Fix DB config.'))
