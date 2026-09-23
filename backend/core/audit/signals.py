"""Authentication events are audited centrally, not at each login view."""
from django.contrib.auth import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .middleware import get_client_ip, get_current_request
from .models import AuditLog
from .services import log


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    request = request or get_current_request()
    ip = get_client_ip(request)
    if ip and getattr(user, "last_login_ip", None) != ip:
        type(user).objects.filter(pk=user.pk).update(last_login_ip=ip)
    log(AuditLog.Action.LOGIN, module="authentication", actor=user, request=request)


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user is None:
        return
    log(AuditLog.Action.LOGOUT, module="authentication", actor=user, request=request)


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request=None, **kwargs):
    from django.contrib.auth import get_user_model

    # Credentials are dropped except for the identifier — never log passwords.
    email = ((credentials or {}).get("username") or "").lower().strip()
    # File the attempt under the account's organization, so its admins can
    # see password guessing against their users. An unknown email stays
    # platform-level: no tenant owns it.
    organization_id = (
        get_user_model()
        .all_objects.filter(email=email)
        .values_list("organization_id", flat=True)
        .first()
    )
    log(
        AuditLog.Action.LOGIN_FAILED,
        module="authentication",
        organization=organization_id,
        metadata={"email": email},
        request=request or get_current_request(),
    )
