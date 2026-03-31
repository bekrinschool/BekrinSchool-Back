"""Users Management API URLs"""
from django.urls import path
from .views_users import (
    users_list_or_create_view,
    users_update_view,
    users_soft_delete_view,
    users_restore_view,
)

urlpatterns = [
    path('', users_list_or_create_view),
    path('<int:pk>/soft_delete/', users_soft_delete_view),
    path('<int:pk>/restore/', users_restore_view),
    path('<int:pk>/', users_update_view),
]
