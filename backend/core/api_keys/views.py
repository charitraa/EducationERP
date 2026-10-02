from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from core.accounts.services import assign_role, revoke_role
from core.common.mixins import OrganizationScopedViewSet
from core.permissions.models import UserRole

from . import services
from .models import ApiKey
from .serializers import (
    ApiKeySerializer,
    CreateApiKeySerializer,
    CreatedApiKeySerializer,
    GrantSerializer,
    RevokeApiKeySerializer,
    RoleGrantSerializer,
)

TAG = "api keys"
MANAGE = "api_keys.manage"


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List API keys"),
    retrieve=extend_schema(tags=[TAG], summary="Retrieve an API key (never its secret)"),
    create=extend_schema(tags=[TAG], summary="Create an API key (the secret is in this response only)",
                         request=CreateApiKeySerializer, responses={201: CreatedApiKeySerializer}),
    partial_update=extend_schema(tags=[TAG], summary="Rename, or change read-only, expiry, addresses or rate"),
    update=extend_schema(tags=[TAG], summary="Replace an API key's settings"),
)
class ApiKeyViewSet(OrganizationScopedViewSet):
    """Keys for programs that use the API (a website, an SMS gateway, a
    reporting tool). A key's access is the roles given to it; managing keys
    needs a person's login, never another key."""

    http_method_names = ["get", "post", "put", "patch", "head", "options"]
    queryset = ApiKey.objects.select_related("user")
    serializer_class = ApiKeySerializer
    audit_module = "api_keys"
    filterset_fields = ["read_only"]
    search_fields = ["name", "prefix"]
    required_permissions = {action: [MANAGE] for action in (
        "list", "retrieve", "create", "update", "partial_update", "revoke", "rotate", "assign_role",
        "revoke_role")}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if isinstance(request.auth, ApiKey):
            raise PermissionDenied("API keys are managed by a person, not by another key.")

    def create(self, request, *args, **kwargs):
        organization_id = self.get_target_organization_id()
        serializer = CreateApiKeySerializer(data=request.data, context={**self.get_serializer_context(),
                                                                      "organization_id": organization_id})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        grants = [(g["role"], g.get("campus")) for g in data.pop("grants", [])]
        key, raw = services.create_api_key(organization_id=organization_id, by=request.user, roles=grants, **data)
        body = ApiKeySerializer(key).data
        body["key"] = raw
        return Response(body, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Revoke (it stops working at once and stays on record)",
                   request=RevokeApiKeySerializer, responses={200: ApiKeySerializer})
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        serializer = RevokeApiKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = services.revoke_api_key(self.get_object(), reason=serializer.validated_data["reason"], by=request.user)
        return Response(ApiKeySerializer(key).data)

    @extend_schema(tags=[TAG], summary="Rotate: a new secret, shown once; the old one stops working",
                   request=None, responses={200: CreatedApiKeySerializer})
    @action(detail=True, methods=["post"])
    def rotate(self, request, pk=None):
        key, raw = services.rotate_api_key(self.get_object(), by=request.user)
        body = ApiKeySerializer(key).data
        body["key"] = raw
        return Response(body)

    def _grant(self, request, key):
        serializer = GrantSerializer(data=request.data, context={"organization_id": key.organization_id})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["role"], serializer.validated_data.get("campus")

    @extend_schema(tags=[TAG], summary="Give the key a role (no more than you hold yourself)",
                   request=GrantSerializer, responses={201: RoleGrantSerializer})
    @action(detail=True, methods=["post"], url_path="assign-role")
    def assign_role(self, request, pk=None):
        key = self.get_object()
        if key.revoked_at is not None:
            raise PermissionDenied("This key is revoked.")
        role, campus = self._grant(request, key)
        assignment = assign_role(user=key.user, role=role, campus=campus, granted_by=request.user)
        return Response(RoleGrantSerializer(assignment).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Take a role away from the key", request=GrantSerializer,
                   responses={204: None})
    @action(detail=True, methods=["post"], url_path="revoke-role")
    def revoke_role(self, request, pk=None):
        key = self.get_object()
        role, campus = self._grant(request, key)
        assignment = UserRole.objects.filter(user=key.user, role=role, campus=campus).first()
        if assignment is None:
            raise NotFound("The key doesn't have that role.")
        revoke_role(assignment=assignment, revoked_by=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

