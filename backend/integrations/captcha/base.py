"""CAPTCHA checks for public, no-login forms (signup, public applications).

One ``verify`` every provider implements the same way. The browser widget
hands the form a token; the server asks the provider whether it is genuine.
Turnstile, hCaptcha and reCAPTCHA share the same "siteverify" protocol
(POST secret + response + remoteip, JSON ``success``), so one function
covers all three. ``off`` accepts everything: for development and tests.

Fails closed: a provider that can't be reached is a failed check, never a
pass, or an outage would switch the protection off.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger("integrations.captcha")

VERIFY_URLS = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
    "recaptcha": "https://www.google.com/recaptcha/api/siteverify",
}
PROVIDERS = ("off", *VERIFY_URLS)
TIMEOUT_SECONDS = 5


def provider() -> str:
    return settings.CAPTCHA_PROVIDER


def is_enabled() -> bool:
    return provider() != "off"


def public_config() -> dict:
    """What a frontend needs to show the widget. The site key is public."""
    return {"provider": provider(), "site_key": settings.CAPTCHA_SITE_KEY if is_enabled() else ""}


def _siteverify(url: str, token: str, remote_ip: str | None) -> bool:
    fields = {"secret": settings.CAPTCHA_SECRET_KEY, "response": token}
    if remote_ip:
        fields["remoteip"] = remote_ip
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 (fixed https URLs)
            body = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("CAPTCHA provider unreachable: %s", exc)
        return False
    if not body.get("success"):
        return False
    # reCAPTCHA v3 scores every request instead of passing or failing it.
    score = body.get("score")
    return score is None or score >= settings.CAPTCHA_MIN_SCORE


def verify(token: str, remote_ip: str | None = None) -> bool:
    name = provider()
    if name == "off":
        return True
    if not token:
        return False
    return _siteverify(VERIFY_URLS[name], token, remote_ip)


def require(request, token: str) -> None:
    """For a view: refuse the request unless the CAPTCHA passes."""
    from core.audit.middleware import get_client_ip
    from core.common.exceptions import ServiceError

    if not verify(token, get_client_ip(request)):
        raise ServiceError("The CAPTCHA check failed. Try again.", code="captcha_failed")
