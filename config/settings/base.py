"""
Django settings for bekrin-back project - Base configuration
"""
import os
from pathlib import Path
import environ

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Environment variables
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

# Read .env file if exists
env_file = BASE_DIR / '.env'
if env_file.exists():
    environ.Env.read_env(env_file)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('DJANGO_SECRET_KEY', default='django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG', default=False)

# Single-tenant: teacher sees all users/groups/payments (no org filter). Set False for multi-tenant.
SINGLE_TENANT = env.bool('SINGLE_TENANT', default=True)

raw_hosts = os.getenv('ALLOWED_HOSTS', '')
if raw_hosts:
    ALLOWED_HOSTS = raw_hosts.split(',')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'drf_spectacular',
    
    # Local apps (core first: Organization used by User)
    'core',
    'accounts',
    'students',
    'groups',
    'attendance',
    'payments',
    'notifications',
    'tests.apps.TestsConfig',
    'coding',
]

USE_S3 = env.bool('USE_S3', default=False)
if USE_S3:
    INSTALLED_APPS.append('storages')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # TEMPORARILY DISABLED to test if middleware corrupts PDF stream (0 of 0 pages). Re-enable after test.
    # 'config.middleware.FrameOptionsExemptMiddleware',  # Allow /media/ and run PDF in iframes
]

# Disable APPEND_SLASH for API endpoints (REST APIs typically don't use trailing slashes)
APPEND_SLASH = False

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database — PostgreSQL only (no SQLite fallback)
# See config/settings/database.py for DATABASE_URL or DB_NAME/DB_USER/... logic
from .database import get_database_config
DATABASES = {
    'default': get_database_config(env),
}

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Baku'
USE_I18N = True
USE_TZ = True

# Redis: django-redis cache (REDIS_URL) + Celery broker/result (explicit env overrides Redis URL)
_redis_url = (env.str('REDIS_URL', default='') or '').strip()
if _redis_url:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': _redis_url,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            },
        }
    }

# Celery: CELERY_BROKER_URL / CELERY_RESULT_BACKEND win over REDIS_URL (split broker vs result DBs)
_celery_broker = (env.str('CELERY_BROKER_URL', default='') or '').strip()
_celery_result = (env.str('CELERY_RESULT_BACKEND', default='') or '').strip()
CELERY_BROKER_URL = _celery_broker or _redis_url or 'redis://127.0.0.1:6379/0'
CELERY_RESULT_BACKEND = _celery_result or CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TASK_TIME_LIMIT = env.int('CELERY_TASK_TIME_LIMIT', default=300)
CELERY_TIMEZONE = TIME_ZONE
# Worker tuning (Railway: scale worker replicas + raise concurrency for CPU-bound tasks)
CELERY_WORKER_CONCURRENCY = env.int('CELERY_WORKER_CONCURRENCY', default=4)
CELERY_WORKER_PREFETCH_MULTIPLIER = env.int('CELERY_WORKER_PREFETCH_MULTIPLIER', default=4)
CELERY_TASK_ACKS_LATE = env.bool('CELERY_TASK_ACKS_LATE', default=True)
CELERY_TASK_REJECT_ON_WORKER_LOST = env.bool('CELERY_TASK_REJECT_ON_WORKER_LOST', default=True)

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

# Media files (leading slash required for correct absolute URLs in API responses)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Optional S3-compatible media (Railway volume is ephemeral; use R2/S3/MinIO in production if needed)
if USE_S3:
    AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default=None)
    AWS_S3_ENDPOINT_URL = env('AWS_S3_ENDPOINT_URL', default=None)
    AWS_S3_ADDRESSING_STYLE = env.str('AWS_S3_ADDRESSING_STYLE', default='auto')
    AWS_DEFAULT_ACL = env.str('AWS_DEFAULT_ACL', default=None)
    AWS_QUERYSTRING_AUTH = env.bool('AWS_QUERYSTRING_AUTH', default=True)
    STORAGES['default'] = {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'}

# Image uploads (core.image_compression) — questions / options
IMAGE_UPLOAD_MAX_WIDTH = env.int('IMAGE_UPLOAD_MAX_WIDTH', default=1200)
IMAGE_UPLOAD_JPEG_QUALITY = env.int('IMAGE_UPLOAD_JPEG_QUALITY', default=78)
# Student exam canvases — stronger compression
CANVAS_IMAGE_MAX_WIDTH = env.int('CANVAS_IMAGE_MAX_WIDTH', default=1200)
CANVAS_IMAGE_JPEG_QUALITY = env.int('CANVAS_IMAGE_JPEG_QUALITY', default=58)
# Management command: cleanup_old_canvas_media — delete canvas image files after N days (graded attempts)
CANVAS_MEDIA_RETENTION_DAYS = env.int('CANVAS_MEDIA_RETENTION_DAYS', default=90)

# Allow larger request body for long situation canvas images (base64)
DATA_UPLOAD_MAX_MEMORY_SIZE = 14 * 1024 * 1024  # 14MB (base64 ~33% overhead over 10MB image)

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'config.exceptions.custom_exception_handler',
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# JWT Settings
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(
        minutes=env.int('JWT_ACCESS_LIFETIME_MINUTES', default=60)
    ),
    'REFRESH_TOKEN_LIFETIME': timedelta(
        days=env.int('JWT_REFRESH_LIFETIME_DAYS', default=7)
    ),
    # Note: BLACKLIST_AFTER_ROTATION requires 'rest_framework_simplejwt.token_blacklist' app
    # For now, we disable rotation to avoid errors
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# CORS Settings
CORS_ALLOWED_ORIGINS = env.list(
    'CORS_ALLOWED_ORIGINS',
    default=['http://localhost:3000']
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# CSRF Settings
raw_origins = os.getenv('CSRF_TRUSTED_ORIGINS', '')
if raw_origins:
    CSRF_TRUSTED_ORIGINS = raw_origins.split(',')
else:
    CSRF_TRUSTED_ORIGINS = []

# Source - https://stackoverflow.com/a/45327676
# Posted by J.Jai, modified by community. See post 'Timeline' for change history
# Retrieved 2026-02-20, License - CC BY-SA 4.0
X_FRAME_OPTIONS = 'SAMEORIGIN'

XS_SHARING_ALLOWED_METHODS = ['POST','GET','OPTIONS', 'PUT', 'DELETE']

# Credential encryption (for ImportedCredentialRecord)
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CREDENTIALS_ENCRYPTION_KEY = env('CREDENTIALS_ENCRYPTION_KEY', default=None)

# API Schema (drf-spectacular)
SPECTACULAR_SETTINGS = {
    'TITLE': 'Bekrin School API',
    'DESCRIPTION': 'DIM imtahanına hazırlıq üçün kurs idarəetmə sistemi API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}
