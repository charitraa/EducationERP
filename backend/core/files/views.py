from django.http import FileResponse
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from core.common.mixins import OrganizationScopedMixin

from . import access, services
from .models import StoredFile
from .serializers import StoredFileSerializer, UploadSerializer

TAG = "files"


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="My uploads"),
    retrieve=extend_schema(tags=[TAG], summary="A file's details"),
    create=extend_schema(tags=[TAG], summary="Upload a file (multipart: file, purpose)",
                         request={"multipart/form-data": UploadSerializer}, responses={201: StoredFileSerializer}),
    destroy=extend_schema(tags=[TAG], summary="Remove an upload nothing uses yet"),
)
class StoredFileViewSet(OrganizationScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Upload first, then name the file's id where a record wants one (a
    résumé). Downloads go through ``download``, after the owning module's
    access check; files are never served from a public URL."""

    queryset = StoredFile.objects.select_related("uploaded_by")
    serializer_class = StoredFileSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = "uploads"
    filterset_fields = ["purpose"]

    def get_throttles(self):
        # Only uploads count against the upload rate; everything keeps the general ceiling.
        throttles = super().get_throttles()
        if self.action != "create":
            throttles = [t for t in throttles if not isinstance(t, ScopedRateThrottle)]
        return throttles

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            return qs.filter(uploaded_by=self.request.user)
        return qs

    def get_object(self):
        stored = super().get_object()
        if not access.can_read(self.request.user, stored):
            raise NotFound()
        return stored

    def create(self, request, *args, **kwargs):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization_id = request.user.organization_id
        if organization_id is None:
            raise NotFound()
        stored = services.store(serializer.validated_data["file"], organization_id=organization_id,
                                purpose=serializer.validated_data["purpose"], by=request.user)
        return Response(StoredFileSerializer(stored).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        services.discard(instance, by=self.request.user)

    @extend_schema(tags=[TAG], summary="Download the file", responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        return file_response(self.get_object())


def file_response(stored: StoredFile) -> FileResponse:
    """Always a download, with the type we sniffed, never rendered inline."""
    response = FileResponse(stored.file.open("rb"), as_attachment=True, filename=stored.name,
                            content_type=stored.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
