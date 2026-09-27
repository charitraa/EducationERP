"""Rules for removing campuses."""
from django.db import models

from core.common.exceptions import ConflictError

from .models import Campus


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
