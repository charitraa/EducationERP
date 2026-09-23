"""
Base settings shared by every environment.

Environment-specific modules (development.py / production.py) import everything
from here and override only what actually differs.
"""
from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

# backend/config/settings/base.py -> backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
SECRET_KEY = config("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=Csv())

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
]

# Phase 1 — identity foundation. Every later module depends on these.
CORE_APPS = [
    "core.common",
    "core.organizations",
    "core.accounts",
    "core.permissions",
    "core.audit",
    "core.authentication",
]

# Business modules, in dependency order: each may use the ones above it.
MODULE_APPS: list[str] = [
    # Phase 2 — student foundation
    "modules.students",
    "modules.parents",
    "modules.staff",
    "modules.admissions",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + CORE_APPS + MODULE_APPS

MIDDLEWARE = [
    # Outermost: everything below it, including failures, is logged with an id.
    "core.common.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Serves /static/ straight from the app container, so the image needs no
    # nginx sidecar to render /admin/ and the DRF docs. No-ops when DEBUG is on
    # and Django's own staticfiles handler takes over.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Must sit above CommonMiddleware so preflight OPTIONS requests get the
    # CORS headers even when another middleware short-circuits the response.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.audit.middleware.AuditContextMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database — overridden per environment (SQLite dev / PostgreSQL prod).
# All business logic goes through the ORM so the engine stays swappable.
# --------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# --------------------------------------------------------------------------
# Passwords / i18n / static
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = config("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --------------------------------------------------------------------------
# CORS / CSRF
# The SPA is served from its own origin, so the browser preflights every API
# call. Origins are an explicit allow-list from the environment — never "*",
# because credentialed requests are rejected by the browser when it is.
# --------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())

# JWT travels in the Authorization header, not a cookie, so credentials stay
# off by default. Flip it on only if you move refresh tokens into cookies.
CORS_ALLOW_CREDENTIALS = config("CORS_ALLOW_CREDENTIALS", default=False, cast=bool)
# Only the API is cross-origin; /admin/ and the docs are same-origin.
CORS_URLS_REGEX = r"^/api/.*$"
CORS_EXPOSE_HEADERS = ["Content-Disposition"]
CORS_PREFLIGHT_MAX_AGE = config("CORS_PREFLIGHT_MAX_AGE", default=3600, cast=int)

# Session-authenticated POSTs from the SPA also need the origin trusted here;
# CORS alone does not satisfy Django's CSRF origin check.
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# --------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Secure by default: every endpoint requires auth unless it opts out.
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    # JSON only by default; development.py adds the browsable API back, so it
    # can never be served in production by inheriting DRF's defaults.
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "DEFAULT_PAGINATION_CLASS": "core.common.pagination.DefaultPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.common.exceptions.api_exception_handler",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ("v1",),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "login": config("THROTTLE_LOGIN", default="10/min"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=config("ACCESS_TOKEN_LIFETIME_MINUTES", default=60, cast=int)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=config("REFRESH_TOKEN_LIFETIME_DAYS", default=7, cast=int)
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "core.authentication.serializers.LoginSerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Education ERP API",
    "DESCRIPTION": (
        "Modular education ERP platform. Phase 1: organizations, campuses, "
        "users, roles, permissions, authentication and audit logs. Phase 2: "
        "students, enrollments, parents, staff and admissions."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v[0-9]",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    # Several models have a `status` field with different choices; give each
    # set its own name so generated clients get StudentStatus, not Status93aEnum.
    "ENUM_NAME_OVERRIDES": {
        "StudentStatusEnum": "modules.students.models.Student.Status",
        "EnrollmentStatusEnum": "modules.students.models.Enrollment.Status",
        "StaffStatusEnum": "modules.staff.models.StaffMember.Status",
        "AdmissionStatusEnum": "modules.admissions.models.Admission.Status",
        "RelationshipEnum": "modules.parents.models.StudentParent.Relationship",
    },
}

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {
            "()": "core.common.logging.RequestContextFilter",
        },
    },
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
        # Used for errors: everything needed to chase a 500 without opening
        # the database — which request, which user, which endpoint.
        "detailed": {
            "format": (
                "{levelname} {asctime} {name} "
                "request_id={request_id} user={user_id} ip={client_ip} "
                "{method} {path}\n{message}"
            ),
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_context"],
        },
        # Replaced by a rotating file handler in production.py. Keeping the
        # name defined here means the logger wiring below is identical in
        # every environment.
        "errors": {
            "class": "logging.StreamHandler",
            "level": "ERROR",
            "formatter": "detailed",
            "filters": ["request_context"],
        },
    },
    "loggers": {
        # Django logs every unhandled view exception here at ERROR with the
        # traceback attached. This is the 500 log.
        "django.request": {
            "handlers": ["console", "errors"],
            "level": "ERROR",
            "propagate": False,
        },
        # Uncaught exceptions escaping the WSGI/ASGI handler itself.
        "django.server": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # Our own code: errors it raises deliberately go to the same file.
        "core": {
            "handlers": ["console", "errors"],
            "level": config("LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
    "root": {"handlers": ["console"], "level": config("LOG_LEVEL", default="INFO")},
}
