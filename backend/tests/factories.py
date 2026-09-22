"""Small, explicit helpers for building test data.

Deliberately plain functions rather than a factory library — one less
dependency, and the test data stays readable.
"""
from core.accounts.models import User
from core.organizations.models import Campus, Organization
from core.permissions.models import Permission, Role, UserRole

DEFAULT_PASSWORD = "Test-pass-12345"


def create_organization(code="test-college", name=None, **kwargs) -> Organization:
    return Organization.objects.create(
        name=name or code.replace("-", " ").title(), code=code, **kwargs
    )


def create_campus(organization, code="main", name=None, **kwargs) -> Campus:
    return Campus.objects.create(
        organization=organization,
        name=name or code.replace("-", " ").title(),
        code=code,
        **kwargs,
    )


def create_user(
    organization=None, email="user@test.edu", password=DEFAULT_PASSWORD, **kwargs
) -> User:
    return User.objects.create_user(
        email=email, password=password, organization=organization, **kwargs
    )


def create_superuser(email="root@platform.test", password=DEFAULT_PASSWORD) -> User:
    return User.objects.create_superuser(email=email, password=password)


def create_role(organization=None, code="custom", permissions=(), **kwargs) -> Role:
    role = Role.objects.create(
        organization=organization,
        code=code,
        name=kwargs.pop("name", code.replace("-", " ").title()),
        **kwargs,
    )
    if permissions:
        role.permissions.set(Permission.objects.filter(code__in=permissions))
    return role


def grant(user, role, campus=None) -> UserRole:
    return UserRole.objects.create(user=user, role=role, campus=campus)


def user_with_permissions(organization, permissions, email="perm@test.edu", **kwargs) -> User:
    """A user holding exactly the given permission codes, via a throwaway role."""
    user = create_user(organization=organization, email=email, **kwargs)
    role = create_role(
        organization=organization,
        code=f"role-{user.pk}",
        permissions=permissions,
    )
    grant(user, role)
    return user
