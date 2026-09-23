from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.mixins import CampusScopedViewSet

from .models import StaffMember
from .selectors import staff_member_for_user
from .serializers import StaffMemberSerializer


@extend_schema_view(
    list=extend_schema(tags=["staff"], summary="List staff"),
    retrieve=extend_schema(tags=["staff"], summary="Retrieve a staff member"),
    create=extend_schema(tags=["staff"], summary="Create a staff member"),
    update=extend_schema(tags=["staff"], summary="Replace a staff member"),
    partial_update=extend_schema(tags=["staff"], summary="Update a staff member"),
    destroy=extend_schema(tags=["staff"], summary="Soft-delete a staff member"),
)
class StaffMemberViewSet(CampusScopedViewSet):
    """The staff directory, scoped to the organization and the caller's campuses."""

    queryset = StaffMember.objects.select_related("campus")
    serializer_class = StaffMemberSerializer
    audit_module = "staff"
    filterset_fields = ["status", "staff_type", "campus"]
    search_fields = ["employee_number", "first_name", "last_name", "designation", "email", "phone"]
    ordering_fields = ["first_name", "last_name", "employee_number", "joined_on", "created_at"]

    required_permissions = {
        "list": ["staff.view"],
        "retrieve": ["staff.view"],
        "create": ["staff.create"],
        "update": ["staff.update"],
        "partial_update": ["staff.update"],
        "destroy": ["staff.delete"],
    }

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=["staff"], summary="The caller's own staff record", responses={200: StaffMemberSerializer})
    @action(detail=False, methods=["get"])
    def me(self, request):
        member = staff_member_for_user(request.user)
        if member is None:
            raise NotFound("No staff record is linked to your account.")
        return Response(StaffMemberSerializer(member, context=self.get_serializer_context()).data)
