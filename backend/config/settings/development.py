"""Local development settings — SQLite, debug on, permissive hosts."""
from .base import *  # noqa: F401,F403
from .base import BASE_DIR, REST_FRAMEWORK as BASE_REST_FRAMEWORK, config

DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = ["*"]

# Local frontend dev servers (Vite / CRA / Next). Override in .env if the
# frontend runs on a different port.
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default=(
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    ),
    cast=Csv(),  # noqa: F405
)
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Copied, not mutated: `from .base import *` shares the dict object, so editing
# it in place would also change the settings any other module sees.
REST_FRAMEWORK = {
    **BASE_REST_FRAMEWORK,
    # The browsable API is useful locally and is deliberately absent in base,
    # so production can never inherit a writable HTML UI.
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ),
    # Session auth makes the browsable API and /admin/ usable while developing.
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
}

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True

