"""Test settings — fast password hashing, in-memory database."""
from .base import *  # noqa: F401,F403
from .base import REST_FRAMEWORK as BASE_REST_FRAMEWORK

DEBUG = False
ALLOWED_HOSTS = ["*"]
# At least 32 bytes, or PyJWT warns about the HMAC key length.
SECRET_KEY = "test-only-secret-key-not-used-anywhere-real-0123456789"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Copied rather than mutated — see development.py. Throttling is effectively
# off so tests that log in repeatedly don't trip the limiter; the dedicated
# throttling test lowers the rate itself.
REST_FRAMEWORK = {  # noqa: F405
    **BASE_REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"login": "10000/min"},
}

LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"handlers": []}}
