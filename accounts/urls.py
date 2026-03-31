"""
URLs for accounts app
"""
from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login', views.login_view, name='login'),
    path('logout', views.logout_view, name='logout'),
    path('me', views.me_view, name='me'),
    path('change-password', views.change_password_view, name='change-password'),
]
