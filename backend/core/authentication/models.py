from django.conf import settings
from django.db import models

from core.common.models import TimeStampedModel


class TwoFactor(TimeStampedModel):
    """A user's authenticator-app (TOTP) second factor.

    Created unconfirmed by setup; it only protects logins once the user has
    proved their app works by entering a code (``confirmed_at``).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="two_factor"
    )
    # The shared secret has to be readable to check codes, so it cannot be
    # hashed. It is only ever returned once, during setup.
    secret = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The last 30-second step a code was accepted for; a code can't be
    # replayed within its validity window.
    last_used_step = models.BigIntegerField(null=True, blank=True)
    # SHA-256 hashes of the unused single-use recovery codes.
    recovery_codes = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "auth_two_factor"

    def __str__(self):
        return f"2FA for {self.user}"

    @property
    def is_enabled(self) -> bool:
        return self.confirmed_at is not None
