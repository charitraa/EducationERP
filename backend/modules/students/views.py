from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.mixins import CampusScopedViewSet

from .models import Student
from .selectors import student_for_user, with_current_enrollment
from .serializers import (
    EnrollmentSerializer,
    StatusChangeSerializer,
    StudentSerializer,
    TransferSerializer,
)
from .services import change_student_status, transfer_student


@extend_schema_view(
    list=extend_schema(tags=["students"], summary="List students"),
    retrieve=extend_schema(tags=["students"], summary="Retrieve a student"),
    create=extend_schema(tags=["students"], summary="Create a student (opens their first enrollment)"),
    update=extend_schema(tags=["students"], summary="Replace a student's details"),
    partial_update=extend_schema(tags=["students"], summary="Update a student's details"),
    destroy=extend_schema(tags=["students"], summary="Soft-delete a student"),
)
class StudentViewSet(CampusScopedViewSet):
    """Student records, scoped to the organization and the caller's campuses."""

    queryset = with_current_enrollment(Student.objects.select_related("campus"))
    serializer_class = StudentSerializer
    audit_module = "students"
    service_audits_create = True
    filterset_fields = ["status", "campus", "gender"]
    search_fields = ["student_number", "first_name", "last_name", "email", "phone"]
    ordering_fields = ["first_name", "last_name", "student_number", "admitted_on", "created_at"]

    required_permissions = {
        "list": ["students.view"],
        "retrieve": ["students.view"],
        "create": ["students.create"],
        "update": ["students.update"],
        "partial_update": ["students.update"],
        "destroy": ["students.delete"],
        "enrollments": ["students.view"],
        "transfer": ["students.change_status"],
        "change_status": ["students.change_status"],
    }

    def get_permissions(self):
        # A student reading their own record needs no permission — being the
        # person the record is about is the authorization.
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=["students"], summary="Enrollment history of a student")
    @action(detail=True, methods=["get"])
    def enrollments(self, request, pk=None):
        student = self.get_object()
        return Response(EnrollmentSerializer(student.enrollments.select_related("campus"), many=True).data)

    @extend_schema(
        tags=["students"],
        summary="Transfer a student to another campus",
        request=TransferSerializer,
        responses={200: StudentSerializer},
    )
    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        student = self.get_object()
        serializer = TransferSerializer(data=request.data, context={"student": student})
        serializer.is_valid(raise_exception=True)
        # Both ends must be covered: the source by get_object's scoping, the
        # destination here.
        self.check_campus_allowed(serializer.validated_data["campus"])

        student = transfer_student(
            student=student,
            to_campus=serializer.validated_data["campus"],
            on_date=serializer.validated_data.get("on_date"),
            reason=serializer.validated_data.get("reason", ""),
            by=request.user,
        )
        return self._student_response(student)

    @extend_schema(
        tags=["students"],
        summary="Suspend, reactivate, graduate or withdraw a student",
        request=StatusChangeSerializer,
        responses={200: StudentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="change-status")
    def change_status(self, request, pk=None):
        student = self.get_object()
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student = change_student_status(
            student=student,
            status=serializer.validated_data["status"],
            on_date=serializer.validated_data.get("on_date"),
            reason=serializer.validated_data.get("reason", ""),
            by=request.user,
        )
        return self._student_response(student)

    @extend_schema(tags=["students"], summary="The caller's own student record", responses={200: StudentSerializer})
    @action(detail=False, methods=["get"])
    def me(self, request):
        student = student_for_user(request.user)
        if student is None:
            raise NotFound("No student record is linked to your account.")
        return Response(StudentSerializer(student, context=self.get_serializer_context()).data)

    def _student_response(self, student):
        student = self.get_queryset().get(pk=student.pk)
        return Response(
            StudentSerializer(student, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )
