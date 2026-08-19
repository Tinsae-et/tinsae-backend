from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .metrics import collect_health


class OpsHealthView(APIView):
    """Internal, staff-only. Not part of the public /api/v1 contract."""

    permission_classes = [IsAdminUser]
    # JWT is the project default, but this endpoint is meant to be opened in a
    # browser alongside the admin page. Without SessionAuthentication an admin
    # who is already logged in still gets a 401 here.
    authentication_classes = [SessionAuthentication, JWTAuthentication]

    def get(self, request):
        return Response(collect_health())


@staff_member_required
def ops_health_page(request):
    """The same dict as the endpoint, rendered for a human."""
    return render(
        request,
        "ops/health.html",
        {"health": collect_health(), "title": "Loop health"},
    )
