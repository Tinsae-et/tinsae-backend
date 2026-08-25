from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)


def revoke_all_refresh_tokens(user):
    """Blacklist every outstanding refresh token for `user`. Returns the count.

    Used wherever the password changes: a password change that leaves old
    sessions alive does not actually evict whoever knew the old password.

    Note what this does *not* do. An access token already in a client's hands
    stays valid until it expires (`ACCESS_TOKEN_LIFETIME`). That is inherent to
    stateless JWT -- checking a blacklist on every request would mean a database
    round trip per API call, which is the cost JWT exists to avoid. The short
    access lifetime is what bounds the window; the refresh chain is what this
    actually severs.
    """
    revoked = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        if created:
            revoked += 1
    return revoked
