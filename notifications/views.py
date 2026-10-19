"""API endpoints for notifications."""

from django.http import Http404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import DefaultPagination

from .models import Notification
from .selectors import notifications_for
from .serializers import NotificationSerializer
from .services import mark_all_read, mark_read, unread_count


class NotificationListView(generics.ListAPIView):
    """List authenticated user's notifications, newest first."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "notifications"
    serializer_class = NotificationSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return notifications_for(self.request.user)


class NotificationUnreadCountView(APIView):
    """Return the unread notification count for the header bell."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "notifications"

    def get(self, request):
        count = unread_count(request.user)
        return Response({"unread_count": count}, status=status.HTTP_200_OK)


class NotificationMarkReadView(APIView):
    """Mark a single notification as read, returning the updated item."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "notifications"

    def post(self, request, pk: int):
        try:
            notification = mark_read(request.user, pk)
        except Notification.DoesNotExist:
            raise Http404("Not found.") from None
        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_200_OK,
        )


class NotificationMarkAllReadView(APIView):
    """Mark all unread notifications as read, returning the count updated."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "notifications"

    def post(self, request):
        updated = mark_all_read(request.user)
        return Response({"updated": updated}, status=status.HTTP_200_OK)
