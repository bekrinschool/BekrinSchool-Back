"""
Core models — Organization (optional tenant for future multi-center).
"""
from django.db import models


class Organization(models.Model):
    """
    Organization / center. Single tenant for now; multi-tenant later.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'organizations'
        verbose_name = 'Organization'
        verbose_name_plural = 'Organizations'
        ordering = ['name']

    def __str__(self):
        return self.name
