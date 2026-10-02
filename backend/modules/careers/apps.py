from django.apps import AppConfig


class CareersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.careers"
    label = "careers"
    verbose_name = "Careers"

    def ready(self):
        from core.files import access
        from core.files.services import register_purpose

        from . import permissions  # noqa: F401  (registers the catalogue)
        from .services import CANDIDACY, can_read_resume

        register_purpose("resume", {"pdf", "docx"})
        access.register(CANDIDACY, can_read_resume)
