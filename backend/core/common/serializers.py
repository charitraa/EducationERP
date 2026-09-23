"""Validation helpers shared by module serializers."""
from rest_framework import serializers


def target_organization_id(serializer) -> int | None:
    """The tenant a serializer is writing into.

    The instance's own organization on update; on create, whatever the view
    resolves (the caller's organization, or the one a platform admin named).
    """
    if serializer.instance is not None:
        return serializer.instance.organization_id
    return serializer.context["view"].get_target_organization_id()


def ensure_unique_in_organization(serializer, field: str, value) -> None:
    """Reject a value already used by a live row of the same organization.

    The database has matching partial unique constraints, but this runs first
    so the caller gets a 400 naming the field rather than an integrity error —
    and so MySQL, which cannot enforce partial constraints, is covered too.
    """
    if value in (None, ""):
        return
    model = serializer.Meta.model
    clash = model.objects.filter(
        organization_id=target_organization_id(serializer), **{field: value}
    )
    if serializer.instance is not None:
        clash = clash.exclude(pk=serializer.instance.pk)
    if clash.exists():
        raise serializers.ValidationError(
            {field: f"This {field.replace('_', ' ')} is already in use in this organization."}
        )


def validate_linked_user(serializer, user, profile_field: str = "user"):
    """A login account linked to a profile must be in the same organization
    and not already linked to another profile of the same kind."""
    if user is None:
        return user
    if user.organization_id != target_organization_id(serializer):
        # Same message as a missing id: never confirm another tenant's user exists.
        raise serializers.ValidationError("Unknown user.")

    model = serializer.Meta.model
    taken = model.all_objects.filter(**{profile_field: user})
    if serializer.instance is not None:
        taken = taken.exclude(pk=serializer.instance.pk)
    if taken.exists():
        raise serializers.ValidationError(
            f"This user is already linked to another {model._meta.verbose_name}."
        )
    return user
