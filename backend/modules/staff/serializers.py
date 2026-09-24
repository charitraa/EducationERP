from django.contrib.auth import get_user_model
from django.db.models import Q
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
        if self.instance is not None and status == StaffMember.Status.LEFT \
                and self.instance.status != StaffMember.Status.LEFT:
            self._check_nothing_left_to_teach(left_on)
        return attrs

    def _check_nothing_left_to_teach(self, left_on):
        """A teacher who leaves mustn't leave classes behind with nobody to
        teach them. Their lessons, planned cover and class-teacher duties
        from the leaving date on are listed so the office can hand them over
        (the timetable's hand-over takes a date) first.

        Reads the timetable and academics through relation names only, so
        the staff module doesn't import the modules that build on it.
        """
        member = self.instance
        running = Q(timetable_entries__valid_until__isnull=True) | Q(timetable_entries__valid_until__gte=left_on)
        lessons = member.teaching_assignments.filter(
            running, timetable_entries__isnull=False, timetable_entries__deleted_at__isnull=True,
        ).values("timetable_entries").distinct().count()
        cover = member.substitutions.filter(
            date__gte=left_on, is_cancelled=False, deleted_at__isnull=True
        ).count()
        classes = list(member.class_teacher_of.filter(
            deleted_at__isnull=True, academic_year__end_date__gte=left_on
        ).select_related("program"))
        if lessons or cover or classes:
            parts = []
            if lessons:
                parts.append(f"{lessons} weekly lessons (hand them over from {left_on})")
            if cover:
                parts.append(f"{cover} lessons they're due to cover")
            if classes:
                parts.append("class teacher of " + ", ".join(c.display_name for c in classes))
            raise serializers.ValidationError(
                {"status": "Still has " + "; ".join(parts) + ". Reassign those first."}
            )
