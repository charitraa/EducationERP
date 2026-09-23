from django.contrib.auth import get_user_model
from rest_framework import serializers

from core.common.serializers import validate_linked_user
from modules.students.selectors import students_visible_to

from .models import Parent, StudentParent


class ParentSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(), required=False, allow_null=True
    )
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Parent
        fields = [
            "id", "organization", "first_name", "middle_name", "last_name", "full_name",
            "phone", "email", "occupation", "address", "user", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_user(self, user):
        return validate_linked_user(self, user)


class ChildSerializer(serializers.ModelSerializer):
    """A student as seen through a parent link."""

    student = serializers.IntegerField(source="student.pk", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    full_name = serializers.CharField(source="student.full_name", read_only=True)
    campus_name = serializers.CharField(source="student.campus.name", read_only=True)
    status = serializers.CharField(source="student.status", read_only=True)

    class Meta:
        model = StudentParent
        fields = [
            "student", "student_number", "full_name", "campus_name", "status",
            "relationship", "is_primary_contact",
        ]
        read_only_fields = fields


class ParentWithChildrenSerializer(ParentSerializer):
    children = serializers.SerializerMethodField()

    class Meta(ParentSerializer.Meta):
        fields = ParentSerializer.Meta.fields + ["children"]

    def get_children(self, parent) -> list:
        from .selectors import links_for_parent

        return ChildSerializer(links_for_parent(parent), many=True).data


class StudentChoiceMixin(serializers.Serializer):
    """A ``student`` field limited to students the caller may see — so an id
    from another organization or an uncovered campus is simply unknown."""

    student = serializers.PrimaryKeyRelatedField(queryset=students_visible_to(None))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        self.fields["student"].queryset = students_visible_to(
            getattr(request, "user", None), "students.view"
        )


class LinkStudentSerializer(StudentChoiceMixin):
    relationship = serializers.ChoiceField(choices=StudentParent.Relationship.choices)
    is_primary_contact = serializers.BooleanField(default=False)


class UnlinkStudentSerializer(StudentChoiceMixin):
    pass
