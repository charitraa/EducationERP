from django.contrib.auth import get_user_model
from rest_framework import serializers

from core.common.serializers import (
    ensure_unique_in_organization,
    target_organization_id,
    validate_linked_user,
)
from core.organizations.models import Campus
from modules.academics.models import Section

from .models import Enrollment, Student
from .selectors import get_current_enrollment


class EnrollmentSerializer(serializers.ModelSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    section_name = serializers.CharField(source="section.display_name", read_only=True, default=None)
    program_name = serializers.CharField(source="section.program.name", read_only=True, default=None)
    academic_year_name = serializers.CharField(
        source="section.academic_year.name", read_only=True, default=None
    )

    class Meta:
        model = Enrollment
        fields = [
            "id", "campus", "campus_name", "section", "section_name", "program_name",
            "academic_year_name", "status", "started_on", "ended_on", "end_reason",
            "created_at",
        ]
        read_only_fields = fields


class StudentSerializer(serializers.ModelSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    user = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(), required=False, allow_null=True
    )
    full_name = serializers.CharField(read_only=True)
    current_enrollment = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            "id", "organization", "student_number", "first_name", "middle_name",
            "last_name", "full_name", "date_of_birth", "gender", "email", "phone",
            "address", "campus", "campus_name", "status", "admitted_on", "user",
            "current_enrollment", "created_at", "updated_at",
        ]
        # Status and campus change only through the change-status and transfer
        # actions, which keep the enrollment history in step.
        read_only_fields = ["id", "organization", "status", "created_at", "updated_at"]

    def get_current_enrollment(self, student) -> dict | None:
        enrollment = get_current_enrollment(student)
        return EnrollmentSerializer(enrollment).data if enrollment else None

    def validate_campus(self, campus):
        if campus.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown campus.")
        if self.instance is not None and campus.pk != self.instance.campus_id:
            raise serializers.ValidationError(
                "Use the transfer action to move a student to another campus."
            )
        return campus

    def validate_user(self, user):
        return validate_linked_user(self, user)

    def validate_student_number(self, value):
        value = value.strip()
        ensure_unique_in_organization(self, "student_number", value)
        return value

    def validate_admitted_on(self, value):
        # The first enrollment starts on this date; moving it afterwards would
        # leave the history saying something different from the student row.
        if self.instance is not None and value != self.instance.admitted_on:
            raise serializers.ValidationError("The admission date cannot be changed.")
        return value

    def create(self, validated_data):
        from .services import create_student

        return create_student(
            organization_id=validated_data.pop("organization_id"),
            created_by=self.context["request"].user,
            **validated_data,
        )


class TransferSerializer(serializers.Serializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    on_date = serializers.DateField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_campus(self, campus):
        if campus.organization_id != self.context["student"].organization_id:
            raise serializers.ValidationError("Unknown campus.")
        return campus


class PlacementSerializer(serializers.Serializer):
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all())
    on_date = serializers.DateField(
        required=False, help_text="When a move takes effect. Default: today. Ignored for a first placement."
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_section(self, section):
        if section.organization_id != self.context["student"].organization_id:
            raise serializers.ValidationError("Unknown section.")
        return section


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Student.Status.choices)
    on_date = serializers.DateField(
        required=False, help_text="When a graduation or withdrawal takes effect. Default: today."
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
