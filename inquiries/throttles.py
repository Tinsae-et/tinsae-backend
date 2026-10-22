from rest_framework.throttling import SimpleRateThrottle


class PhoneRevealUserThrottle(SimpleRateThrottle):
    """Tight, per-user limit on phone reveals to prevent automated scraping."""

    scope = "phone_reveal"

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        # Only throttle phone reveals, not telegram or in-app intents
        if request.data.get("channel") != "phone":
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": request.user.pk,
        }
