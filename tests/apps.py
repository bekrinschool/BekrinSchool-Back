from django.apps import AppConfig


class TestsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tests'

    def ready(self):
        from django.db.models.signals import post_delete
        from .models import ExamRun
        from . import signals
        from . import signals_media  # noqa: F401 - registers media lifecycle + image compression
        post_delete.connect(signals.delete_run_images, sender=ExamRun)
