"""Serializers for the notifications API."""

from rest_framework import serializers

from .kinds import KINDS, InApp
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    type = serializers.SerializerMethodField()
    unread = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()
    cta = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "kind",
            "type",
            "unread",
            "created_at",
            "title",
            "body",
            "cta",
        ]
        read_only_fields = fields

    def _get_in_app(self, obj: Notification) -> InApp | None:
        if not hasattr(obj, "_in_app_cache"):
            spec = KINDS.get(obj.kind)
            obj._in_app_cache = (
                spec.in_app(obj.payload) if spec and spec.in_app else None
            )
        return obj._in_app_cache

    def get_type(self, obj: Notification) -> str:
        in_app = self._get_in_app(obj)
        return in_app.type if in_app else ""

    def get_unread(self, obj: Notification) -> bool:
        return obj.read_at is None

    def get_title(self, obj: Notification) -> str:
        in_app = self._get_in_app(obj)
        return in_app.title if in_app else ""

    def get_body(self, obj: Notification) -> str:
        in_app = self._get_in_app(obj)
        return in_app.body if in_app else ""

    def get_cta(self, obj: Notification) -> dict:
        in_app = self._get_in_app(obj)
        label = in_app.cta_label if in_app else ""
        return {"label": label, "href": obj.link}
