"""
Fix users whose password was saved as plain text (admin bug).
Re-hashes plain text passwords so login works again.
Usage: python manage.py fix_plain_passwords
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Re-hash users whose password is stored as plain text (fixes login mismatch)'

    def handle(self, *args, **options):
        fixed = 0
        for user in User.objects.all():
            pw = user.password
            # Django hashes start with algorithm identifier (pbkdf2_sha256$, argon2$, etc.)
            if not pw or (not pw.startswith('pbkdf2_') and not pw.startswith('argon2') and not pw.startswith('bcrypt')):
                try:
                    user.set_password(pw)
                    user.save(update_fields=['password'])
                    fixed += 1
                    self.stdout.write(self.style.SUCCESS(f'Fixed: {user.email}'))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'Failed {user.email}: {e}'))
        self.stdout.write(self.style.SUCCESS(f'Done. Fixed {fixed} user(s).'))
