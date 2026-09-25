"""Device API keys: ``Authorization: Device <key>``. Only a hash is stored."""
import hashlib
import secrets

from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import authentication, exceptions

from modules.attendance.models import AttendanceDevice

PREFIX_LENGTH = 8


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def set_new_key(device) -> str:
    """Give ``device`` a new key and return it; the old one stops working."""
    key = secrets.token_urlsafe(32)
    device.key_hash, device.key_prefix = _hash(key), key[:PREFIX_LENGTH]
    device.save(update_fields=["key_hash", "key_prefix", "updated_at"])
    return key


def client_ip(request) -> str:
    from core.audit.middleware import get_client_ip

    return get_client_ip(request) or ""


def ip_allowed(device, request) -> bool:
    return not device.allowed_ips or client_ip(request) in device.allowed_ips


class DevicePrincipal:
    """Stands in for ``request.user`` on device requests."""

    is_authenticated = True
    is_active = True
    is_superuser = False
    is_platform_admin = False

    def __init__(self, device):
        self.device = device
        self.organization_id = device.organization_id
        self.pk = f"device-{device.pk}"  # rate limits count per device


class DeviceAuthentication(authentication.BaseAuthentication):
    keyword = "Device"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode(errors="replace").split()
        if not header or header[0] != self.keyword:
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid device credentials.")
        device = (AttendanceDevice.objects.filter(key_hash=_hash(header[1]), is_active=True,
                                                  kind=AttendanceDevice.Kind.GENERIC)
                  .select_related("organization", "campus").first())
        if device is None or not ip_allowed(device, request):
            raise exceptions.AuthenticationFailed("Invalid device credentials.")
        AttendanceDevice.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
        return DevicePrincipal(device), None

    def authenticate_header(self, request):
        return self.keyword


class DeviceAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents the device key in the OpenAPI schema."""

    target_class = "integrations.biometric.auth.DeviceAuthentication"
    name = "DeviceKey"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "Authorization",
                "description": "`Device <key>`, the key shown when the device was registered."}
