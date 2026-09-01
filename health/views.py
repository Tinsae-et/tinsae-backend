from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    """Liveness check. Confirms the app is up and the database is reachable.

    The status code carries the verdict, not just the body. Load balancers,
    container orchestrators and uptime monitors all route on the code alone, so
    a 200 with `"database": "error"` is an unhealthy instance that keeps being
    handed traffic -- the outage stays invisible to everything watching for it.
    """
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False

    return Response(
        {
            "status": "ok" if db_ok else "unhealthy",
            "database": "ok" if db_ok else "error",
        },
        status=status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
