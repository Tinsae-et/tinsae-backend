from django.apps import AppConfig


class InquiriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inquiries"

    def ready(self):
        from . import signals  # noqa: F401
