from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .validators import normalize_email, normalize_phone


def resolve_user(identifier):
    """Find the user an identifier refers to, routing by *shape*.

        contains "@"                -> email
        parses as an Ethiopian mobile -> phone
        anything else               -> username (case-insensitive)

    Deliberately not a three-way OR. `UnicodeUsernameValidator` permits `@` and
    `.`, so a username may legally look like an email address. Under
    `Q(username=x) | Q(email=x)` anyone could register the username
    `sara@shop.et` and make the real owner of that *email* ambiguous; whichever
    row the query returned first, the other account could never log in again.
    That is an account-denial vector open to any visitor. One input shape, one
    column, no ambiguity -- and registration additionally refuses usernames of
    the reserved shapes so nobody creates an account they cannot reach.

    Returns None rather than raising for unparseable input: callers here are
    authentication paths, where "no such user" and "malformed identifier" must be
    indistinguishable to the caller.
    """
    UserModel = get_user_model()
    identifier = str(identifier).strip()
    if not identifier:
        return None

    if "@" in identifier:
        email = normalize_email(identifier)
        if not email:
            return None
        return UserModel.objects.filter(email__iexact=email).first()

    try:
        phone = normalize_phone(identifier)
    except Exception:
        phone = None
    if phone:
        return UserModel.objects.filter(phone=phone).first()

    return UserModel.objects.filter(username__iexact=identifier).first()


class MultiIdentifierBackend(ModelBackend):
    """Authenticate against username, email or phone.

    Subclasses `ModelBackend` rather than `BaseBackend` so permission handling
    (`has_perm`, `get_all_permissions`, `user_can_authenticate`) is inherited
    untouched -- the admin and `IsAdminUser` behave exactly as before. Accepting
    the plain `username=` kwarg means Django's admin login form keeps working,
    and admins gain email login for free.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = (
            kwargs.get("identifier")
            or username
            or kwargs.get("email")
            or kwargs.get("phone")
        )
        if identifier is None or password is None:
            return None

        user = resolve_user(identifier)
        if user is None:
            # Same reason as ModelBackend's own no-user branch (Django #20760).
            # Without a hash on this path, "no such account" returns measurably
            # sooner than "wrong password" and login becomes a timing oracle for
            # which accounts exist -- exactly what `/auth/token/`'s single
            # generic error message exists to prevent.
            get_user_model()().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
