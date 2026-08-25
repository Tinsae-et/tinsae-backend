import hashlib

from rest_framework.throttling import SimpleRateThrottle

from .backends import resolve_user


def submitted_identifier(request):
    """The login identifier off the request, whichever field name carried it."""
    data = getattr(request, "data", None)
    if not hasattr(data, "get"):
        # A JSON list or a parse failure. Nothing to key on.
        return ""
    value = data.get("identifier") or data.get("username") or ""
    return str(value).strip()


class LoginIPThrottle(SimpleRateThrottle):
    """Loose, per-IP. Catches a scanner without punishing a shared egress IP.

    Ethiopian carriers NAT aggressively, so this rate has to tolerate a whole
    neighbourhood behind one address. It is not what protects an account --
    `LoginAccountThrottle` is.
    """

    scope = "login_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class LoginAccountThrottle(SimpleRateThrottle):
    """Tight, per-account, regardless of how many source IPs are used.

    Keyed on the resolved user's pk, not the submitted text: one account is
    reachable as a username, an email and a phone, and keying on the raw string
    would hand an attacker three independent budgets against the same account.

    Unresolvable input falls back to a hash of the normalised text, so guessing
    at accounts that do not exist stays bounded too. It is hashed because the
    fallback value is attacker-controlled and ends up in a cache key.

    Costs one indexed lookup per login attempt. The login was going to do that
    lookup anyway; this just moves the first one earlier.
    """

    scope = "login_account"

    def get_cache_key(self, request, view):
        identifier = submitted_identifier(request)
        if not identifier:
            # No identifier to attribute the attempt to. LoginIPThrottle still
            # applies, and the request is a 400 regardless.
            return None

        user = resolve_user(identifier)
        if user is not None:
            ident = f"pk{user.pk}"
        else:
            digest = hashlib.sha256(identifier.lower().encode()).hexdigest()
            ident = f"raw{digest[:32]}"

        return self.cache_format % {"scope": self.scope, "ident": ident}
