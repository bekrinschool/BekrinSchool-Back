"""
Delete stored snapshot files for exam attempt canvases after retention period.
Keeps strokes_json / canvas_json for audit; clears ImageField in DB without re-triggering file delete signals.
Schedule via cron, e.g. weekly:
  python manage.py cleanup_old_canvas_media
"""
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from tests.models import ExamAttemptCanvas


def _safe_delete(name: str) -> None:
    if not name:
        return
    try:
        default_storage.delete(name)
    except Exception:
        pass


class Command(BaseCommand):
    help = "Remove canvas snapshot images for graded attempts older than CANVAS_MEDIA_RETENTION_DAYS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override CANVAS_MEDIA_RETENTION_DAYS (default from settings).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print how many rows would be cleared without deleting files or updating DB.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = int(getattr(settings, "CANVAS_MEDIA_RETENTION_DAYS", 90))
        dry = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        qs = (
            ExamAttemptCanvas.objects.filter(
                attempt__finished_at__isnull=False,
                attempt__finished_at__lt=cutoff,
            )
            .filter(Q(attempt__is_checked=True) | Q(attempt__is_result_published=True))
            .exclude(image="")
            .exclude(image__isnull=True)
        )

        total = qs.count()
        processed = 0
        for c in qs.iterator():
            name = c.image.name if c.image else ""
            if not name:
                continue
            if dry:
                processed += 1
                continue
            _safe_delete(name)
            ExamAttemptCanvas.objects.filter(pk=c.pk).update(image=None)
            processed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"cleanup_old_canvas_media: retention={days}d cutoff={cutoff.isoformat()} "
                f"candidates={total} processed={processed} dry_run={dry}"
            )
        )
