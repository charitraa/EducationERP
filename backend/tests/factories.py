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


def user_with_system_role(
    organization, role_code, email="role@test.edu", campus=None, **kwargs
) -> User:
    """A user holding one of the shipped roles (org-admin, campus-admin, ...).

    Needs the catalogue synced first — APITestCaseBase does that.
    """
    user = create_user(organization=organization, email=email, **kwargs)
    grant(user, Role.objects.get(code=role_code, organization=None), campus=campus)
    return user


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------
def create_student(campus, student_number="S-001", **fields):
    """Through the service, so the first enrollment is opened like in real use."""
    from modules.students.services import create_student as create

    fields.setdefault("first_name", "Test")
    fields.setdefault("last_name", "Student")
    return create(
        organization_id=campus.organization_id,
        campus=campus,
        student_number=student_number,
        **fields,
    )


def create_parent(organization, first_name="Test", **fields):
    from modules.parents.models import Parent

    return Parent.objects.create(organization=organization, first_name=first_name, **fields)


def create_staff_member(campus, employee_number="E-001", **fields):
    from modules.staff.models import StaffMember

    fields.setdefault("first_name", "Test")
    fields.setdefault("last_name", "Staff")
    return StaffMember.objects.create(
        organization_id=campus.organization_id,
        campus=campus,
        employee_number=employee_number,
        **fields,
    )


def create_admission(campus, application_number="A-001", **fields):
    from modules.admissions.models import Admission

    fields.setdefault("first_name", "Test")
    fields.setdefault("last_name", "Applicant")
    return Admission.objects.create(
        organization_id=campus.organization_id,
        campus=campus,
        application_number=application_number,
        **fields,
    )


# ---------------------------------------------------------------------------
# Phase 3 — academics
# ---------------------------------------------------------------------------
def create_program(organization, code="plus2-science", name="+2 Science", **fields):
    from modules.academics.models import Program

    fields.setdefault("level_type", "grade")
    fields.setdefault("first_level", 11)
    fields.setdefault("last_level", 12)
    return Program.objects.create(organization=organization, code=code, name=name, **fields)


def create_subject(organization, code="physics", name="Physics", **fields):
    from modules.academics.models import Subject

    return Subject.objects.create(organization=organization, code=code, name=name, **fields)


def add_to_curriculum(program, subject, level, **fields):
    from modules.academics.models import CurriculumSubject

    return CurriculumSubject.objects.create(
        organization_id=program.organization_id, program=program, subject=subject, level=level, **fields
    )


def create_academic_year(organization, name="2082/83", start=None, end=None, **fields):
    """By default a year that contains today, so tests don't expire with the
    calendar. Dates may be given as ISO strings."""
    from datetime import date, timedelta

    from modules.academics.models import AcademicYear

    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    start = start or date.today() - timedelta(days=60)
    end = end or start + timedelta(days=364)
    return AcademicYear.objects.create(
        organization=organization, name=name, start_date=start, end_date=end, **fields
    )


def create_section(campus, program, academic_year, level=11, name="A", **fields):
    from modules.academics.models import Section

    return Section.objects.create(
        organization_id=campus.organization_id, campus=campus, program=program,
        academic_year=academic_year, level=level, name=name, **fields,
    )
