#!/usr/bin/env python
"""Reproduce 500 error on coding-monitor endpoint."""
import os
import sys
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()

from django.test import RequestFactory
from accounts.models import User
from coding.views.teacher import teacher_coding_monitor_view
from rest_framework_simplejwt.tokens import RefreshToken

user = User.objects.filter(role='teacher').first()
if not user:
    print("No teacher found")
    exit(1)
token = RefreshToken.for_user(user)
req = RequestFactory().get(
    '/api/teacher/coding-monitor',
    {'groupId': '7', 'page': '1', 'page_size': '20', 'sort': 'last_activity'}
)
req.META['HTTP_AUTHORIZATION'] = f'Bearer {str(token.access_token)}'
req.user = user

try:
    resp = teacher_coding_monitor_view(req)
    print(f"Status: {resp.status_code}")
    print(resp.data)
except Exception as e:
    import traceback
    traceback.print_exc()
