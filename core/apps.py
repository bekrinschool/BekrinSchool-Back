import logging
from django.apps import AppConfig
from django.db import connection

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Core (Organization)'

    def ready(self):
        try:
            vendor = connection.vendor
            logger.info('DB=%s', vendor)
            # Visible at runserver startup when LOG level is INFO or lower
            if vendor:
                print(f'[startup] DB={vendor}')
        except Exception:
            pass
