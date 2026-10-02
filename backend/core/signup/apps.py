from django.apps import AppConfig


class SignupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.signup"
    label = "signup"
    verbose_name = "Public signup"
