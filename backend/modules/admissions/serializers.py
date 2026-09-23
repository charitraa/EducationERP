from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, target_organization_id
from core.organizations.models import Campus

from .models import Admission


class AdmissionSerializer(serializers.ModelSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Admission
        fields = [
            "id", "organization", "application_number", "applied_on", "applying_for",
            "campus", "campus_name", "first_name", "middle_name", "last_name", "full_name",
            "date_of_birth", "gender", "email", "phone", "address", "previous_school",
            "guardian_first_name", "guardian_last_name", "guardian_relationship",
            "guardian_phone", "guardian_email",
            "status", "decided_at", "decided_by", "decision_note", "student",
            "created_at", "updated_at",
        ]
        # The workflow fields move only through the approve / reject /
        # withdraw / enroll actions.
        read_only_fields = [
            "id", "organization", "status", "decided_at", "decided_by",
            "decision_note", "student", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        if self.instance is not None and not self.instance.is_open:
            raise serializers.ValidationError(
                f"A {self.instance.status} application can no longer be edited."
            )
        return attrs

    def validate_campus(self, campus):
        if campus.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown campus.")
        return campus

    def validate_application_number(self, value):
        value = value.strip()
        ensure_unique_in_organization(self, "application_number", value)
        return value


class DecisionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


class EnrollSerializer(serializers.Serializer):
    student_number = serializers.CharField(max_length=32)
    started_on = serializers.DateField(
        required=False, help_text="First day of study. Default: today."
    )
