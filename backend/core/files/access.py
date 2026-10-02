"""Who may read an attached file is up to the module it is attached to.

Each module registers a check for its own ``owner_type``::

    register("careers.candidacy", lambda user, owner_id: ...)

An owner type nobody registered is readable by nobody, so a forgotten
registration fails closed.
"""
_CHECKS = {}


def register(owner_type: str, check) -> None:
    _CHECKS[owner_type] = check


def can_read(user, stored_file) -> bool:
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if stored_file.organization_id != user.organization_id:
        return False
    if not stored_file.is_attached:
        return stored_file.uploaded_by_id == user.pk
    check = _CHECKS.get(stored_file.owner_type)
    return bool(check and check(user, stored_file.owner_id))
