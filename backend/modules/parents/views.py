from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.mixins import OrganizationScopedViewSet
from modules.students.selectors import students_visible_to

from .models import Parent
from .selectors import links_for_parent, parent_for_user
from .serializers import (
    ChildSerializer,
    LinkStudentSerializer,
    ParentSerializer,
    ParentWithChildrenSerializer,
    UnlinkStudentSerializer,
)
from .services import link_student, unlink_student


@extend_schema_view(
    list=extend_schema(
        tags=["parents"],
        summary="List parents",
        parameters=[OpenApiParameter("student", int, description="Only parents of this student")],
    ),
    retrieve=extend_schema(tags=["parents"], summary="Retrieve a parent"),
    create=extend_schema(tags=["parents"], summary="Create a parent"),
    update=extend_schema(tags=["parents"], summary="Replace a parent"),
    partial_update=extend_schema(tags=["parents"], summary="Update a parent"),
    destroy=extend_schema(tags=["parents"], summary="Soft-delete a parent"),
)
class ParentViewSet(OrganizationScopedViewSet):
    """Parents and guardians, and which students they are responsible for."""

    queryset = Parent.objects.all()
    serializer_class = ParentSerializer
    audit_module = "parents"
    search_fields = ["first_name", "last_name", "phone", "email"]
    ordering_fields = ["first_name", "last_name", "created_at"]

    required_permissions = {
        "list": ["parents.view"],
        "retrieve": ["parents.view"],
        "create": ["parents.create"],
        "update": ["parents.update"],
        "partial_update": ["parents.update"],
        "destroy": ["parents.delete"],
        "students": ["parents.view"],
        "link_student": ["parents.update"],
        "unlink_student": ["parents.update"],
    }

    def get_permissions(self):
        # Parents reading their own profile and children need no permission.
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        student_id = self.request.query_params.get("student")
        if student_id and self.action == "list":
            if not str(student_id).isdigit():
                return qs.none()
            qs = qs.filter(student_links__student_id=student_id).distinct()
        return qs

    @extend_schema(tags=["parents"], summary="Students linked to a parent", responses={200: ChildSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def students(self, request, pk=None):
        parent = self.get_object()
        # Only children the caller could see through the students API.
        links = links_for_parent(parent, students=students_visible_to(request.user))
        return Response(ChildSerializer(links, many=True).data)

    @extend_schema(
        tags=["parents"],
        summary="Link a student to a parent",
        request=LinkStudentSerializer,
        responses={201: ChildSerializer},
    )
    @action(detail=True, methods=["post"], url_path="link-student")
    def link_student(self, request, pk=None):
        parent = self.get_object()
        serializer = LinkStudentSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        link = link_student(parent=parent, by=request.user, **serializer.validated_data)
        return Response(ChildSerializer(link).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["parents"],
        summary="Unlink a student from a parent",
        request=UnlinkStudentSerializer,
        responses={204: None},
    )
    @action(detail=True, methods=["post"], url_path="unlink-student")
    def unlink_student(self, request, pk=None):
        parent = self.get_object()
        serializer = UnlinkStudentSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        unlink_student(parent=parent, student=serializer.validated_data["student"], by=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["parents"],
        summary="The caller's own parent profile and children",
        responses={200: ParentWithChildrenSerializer},
    )
    @action(detail=False, methods=["get"])
    def me(self, request):
        parent = parent_for_user(request.user)
        if parent is None:
            raise NotFound("No parent profile is linked to your account.")
        return Response(
            ParentWithChildrenSerializer(parent, context=self.get_serializer_context()).data
        )
