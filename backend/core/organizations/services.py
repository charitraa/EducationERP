"""Creating organizations, and the rules for removing campuses."""
from django.db import models, transaction

from core.common.exceptions import ConflictError, ServiceError

from .models import Campus, Organization


@transaction.atomic
def create_organization(*, name: str, code: str, admin_email: str, admin_password: str | None = None,
                        admin_password_hash: str | None = None, type: str = Organization.Type.COLLEGE,
                        campus_name: str = "Main Campus", campus_code: str = "main",
                        admin_first_name: str = "Organization", admin_last_name: str = "Administrator",
                        admin_phone: str = "", **fields) -> tuple[Organization, Campus, "User"]:  # noqa: F821
    """A new tenant: the organization, its main campus and its first
    administrator holding ``org-admin``. ``admin_password_hash`` is an
    already-hashed password (a verified signup keeps only the hash)."""
    from core.accounts.services import create_user
    from core.permissions.models import Role

    code = code.lower()
    if Organization.all_objects.filter(code=code).exists():
        raise ConflictError(f"Organization '{code}' already exists.", code="code_taken")
    if not Role.objects.filter(code="org-admin", organization=None).exists():
        raise ServiceError("System roles are missing. Run 'manage.py sync_permissions' first.",
                           code="roles_missing")

    organization = Organization.objects.create(name=name, code=code, type=type, **fields)
    campus = Campus.objects.create(organization=organization, name=campus_name, code=campus_code.lower(),
                                   is_main=True)
    admin = create_user(email=admin_email, password=admin_password, organization=organization,
                        user_type="administrator", first_name=admin_first_name, last_name=admin_last_name,
                        phone=admin_phone, role_codes=["org-admin"])
    if admin_password_hash:
        admin.password = admin_password_hash
        admin.save(update_fields=["password"])
    return organization, campus, admin


def campus_dependants(campus: Campus) -> dict[str, int]:
    """Live records that still belong to ``campus``, by model name.

    Every model that points at a campus does so with ``PROTECT``, but a soft
    delete never reaches the database, so that protection has to be applied
    here. It reads the relations off the model, so a module added later is
    covered without touching this function. Roles narrowed to the campus don't
    count: they only grant access to the campus itself.
    """
    found: dict[str, int] = {}
    for relation in Campus._meta.get_fields(include_hidden=True):
        if not (relation.auto_created and not relation.concrete and relation.one_to_many):
            continue
        if relation.on_delete is not models.PROTECT:
            continue
        model = relation.related_model
        # The default manager leaves soft-deleted rows out.
        count = model._default_manager.filter(**{relation.field.name: campus}).count()
        if count:
            name = model._meta.verbose_name_plural
            found[str(name)] = found.get(str(name), 0) + count
    return found


def ensure_campus_deletable(campus: Campus) -> None:
    dependants = campus_dependants(campus)
    if dependants:
        listing = ", ".join(f"{count} {name}" for name, count in sorted(dependants.items()))
        raise ConflictError(
            f"This campus still has {listing}. Move or remove them first.",
            code="campus_in_use",
            details=dependants,
        )
