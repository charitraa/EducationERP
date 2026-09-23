"""Production settings — PostgreSQL and hardened security defaults."""
import copy
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, LOGGING as BASE_LOGGING, config

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
        "CONN_MAX_AGE": config("DB_CONN_MAX_AGE", default=60, cast=int),
    }
}

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True

# --------------------------------------------------------------------------
# Logging — 500s must outlive the process that raised them.
#
# The console handler alone is not enough here: under gunicorn it lands in
# whatever the process manager captured, which is rotated, truncated or
# discarded on redeploy. Errors also go to a rotating file whose path is
# stable and mountable on a volume.
# --------------------------------------------------------------------------
LOG_DIR = Path(config("LOG_DIR", default=str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Deep-copied because `from .base import *` shares the nested dicts; editing
# them in place would also change what the other settings modules see.
LOGGING = copy.deepcopy(BASE_LOGGING)
LOGGING["handlers"]["errors"] = {
    "class": "logging.handlers.RotatingFileHandler",
    "level": "ERROR",
    "formatter": "detailed",
    "filters": ["request_context"],
    "filename": str(LOG_DIR / "errors.log"),
    "maxBytes": config("LOG_MAX_BYTES", default=10 * 1024 * 1024, cast=int),
    "backupCount": config("LOG_BACKUP_COUNT", default=10, cast=int),
    "encoding": "utf-8",
    # Several gunicorn workers hold this file open at once. Delay opening
    # until the first error so forked workers each get their own handle.
    "delay": True,
}

# Optional email alerting. Off unless ADMINS is set, so a noisy deploy cannot
# accidentally mail on every request.
ADMINS = [("Ops", email) for email in config("ADMINS", default="", cast=Csv())]  # noqa: F405
if ADMINS and EMAIL_HOST:  # noqa: F405
    LOGGING["handlers"]["mail_admins"] = {
        "class": "django.utils.log.AdminEmailHandler",
        "level": "ERROR",
        "filters": ["request_context"],
        "include_html": False,
    }
    LOGGING["loggers"]["django.request"]["handlers"].append("mail_admins")
