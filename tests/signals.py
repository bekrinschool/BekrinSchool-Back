import logging
import os
import shutil

from django.conf import settings

logger = logging.getLogger(__name__)


def delete_run_images(sender, instance, **kwargs):
    """
    Prevent storage bloat: delete generated PDF page images when an ExamRun is deleted.
    Connected in tests.apps.TestsConfig.ready() with sender=ExamRun.
    """
    run_dir = os.path.join(settings.MEDIA_ROOT, "exam_pages", f"run_{instance.id}")
    try:
        if os.path.exists(run_dir):
            shutil.rmtree(run_dir)
            logger.info("Deleted image folder for run %s", instance.id)
    except Exception:
        logger.error("Failed deleting image folder for run %s (%s)", instance.id, run_dir, exc_info=True)

