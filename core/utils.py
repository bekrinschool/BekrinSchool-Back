"""
Core utilities — organization scoping, etc.
"""


def _user_org(user):
    return getattr(user, 'organization', None) or getattr(user, 'organization_id', None)


def belongs_to_user_organization(obj, user, org_attr='organization'):
    """
    Check if object belongs to user's organization.
    Returns True if user has no org (single-tenant) or obj's org matches user's org.
    """
    user_org = _user_org(user)
    if user_org is None:
        return True
    obj_org = getattr(obj, org_attr, None)
    if obj_org is None:
        return True
    return obj_org == user_org or (hasattr(obj_org, 'pk') and obj_org.pk == getattr(user_org, 'pk', user_org))


def filter_by_organization(queryset, user, org_field='organization'):
    """
    Filter queryset by user's organization.
    Single-tenant mode (SINGLE_TENANT=True): return all, no filter.
    When user has no org: return all (legacy single-tenant).
    Otherwise: filter by user's org.
    """
    from django.conf import settings
    if getattr(settings, 'SINGLE_TENANT', True):
        return queryset
    org = _user_org(user)
    if org is None:
        return queryset
    return queryset.filter(**{f'{org_field}__isnull': False, org_field: org})
