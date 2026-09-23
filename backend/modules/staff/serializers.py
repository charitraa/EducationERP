from django.contrib.auth import get_user_model
from rest_framework import serializers

from core.common.serializers import (
    ensure_unique_in_organization,
    target_organization_id,
    validate_linked_user,
)
from core.organizations.models import Campus

from .models import StaffMember


class StaffMemberSerializer(serializers.ModelSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    user = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(), required=False, allow_null=True
    )
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = StaffMember
        fields = [
            "id", "organization", "employee_number", "first_name", "middle_name",
            "last_name", "full_name", "date_of_birth", "gender", "email", "phone",
            "address", "campus", "campus_name", "staff_type", "designation", "status",
            "joined_on", "left_on", "user", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_campus(self, campus):
        if campus.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown campus.")
        return campus

    def validate_user(self, user):
        return validate_linked_user(self, user)

    def validate_employee_number(self, value):
        value = value.strip()
        ensure_unique_in_organization(self, "employee_number", value)
        return value

    def validate(self, attrs):
        # Mirrors the database check constraints so the caller gets a 400
        # naming the field, not an integrity error.
        def current(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None) if self.instance else None

        status, left_on, joined_on = current("status"), current("left_on"), current("joined_on")
        status = status or StaffMember.Status.ACTIVE
        if status == StaffMember.Status.LEFT and left_on is None:
            raise serializers.ValidationError({"left_on": "Required when the status is 'left'."})
        if status != StaffMember.Status.LEFT and left_on is not None:
            raise serializers.ValidationError({"left_on": "Only allowed when the status is 'left'."})
        if left_on and joined_on and left_on < joined_on:
            raise serializers.ValidationError({"left_on": "Cannot be before the joining date."})
        return attrs
