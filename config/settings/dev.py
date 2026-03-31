"""
Development settings
"""
from .base import *

DEBUG = True

# Development-specific apps
INSTALLED_APPS += [
    # Add dev-only apps here if needed
]

# Email backend (console for development)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
