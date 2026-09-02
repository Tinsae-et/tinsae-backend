from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from core.models import BaseModel

from .validators import normalize_email, normalize_phone


class User(AbstractUser):
    """A merchant account.

    `username` remains `USERNAME_FIELD`, but username, email and phone are all
    unique and any of the three can be used to log in -- see
    `users.backends.MultiIdentifierBackend`.

    `email` and `phone` are `null=True`, not merely `blank=True`. Both carry a
    unique constraint, and Postgres treats NULLs as distinct while two empty
    strings collide; stored as `""`, exactly one user in the table could have no
    phone. Neither is required by the database -- requirement is a serializer
    concern, which is where product policy belongs. `RegistrationSerializer`
    requires an email of everyone (it is the only channel that can deliver a
    verification code) and a phone only of merchants, whose shop has to be
    callable. The columns stay nullable for rows that predate those rules.
    """

    email = models.EmailField(
        "email address", max_length=254, unique=True, null=True, blank=True
    )
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    telegram_chat_id = models.CharField(max_length=50, blank=True)

    # Profile fields. All display-only: nothing in the app routes, filters or
    # authenticates on any of them.
    full_name = models.CharField(max_length=150, blank=True, default="")
    preferred_area = models.CharField(max_length=100, blank=True, default="")
    # Free text the user typed, never verified, and deliberately not unique -- a
    # unique constraint on an unverified handle just lets the first person to
    # type it lock the real owner out. It must never be used to reach anyone:
    # what actually reaches a merchant is `Shop.telegram_chat_id`, obtained
    # through the bot's link-code flow.
    telegram_username = models.CharField(max_length=100, blank=True, default="")
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)

    class Meta:
        # Not `email`: it is nullable by design (see the class docstring, and a
        # large share of merchants have none), and Postgres gives no defined
        # order among NULLs. A paginated list of users could therefore show the
        # same row on two pages and never show another at all, and vary between
        # identical requests. `username` is non-null and unique.
        ordering = ["username"]
        constraints = [
            # `username` already has a plain unique index from AbstractUser. This
            # adds case-insensitivity, so `Sara` and `sara` cannot both exist --
            # which is what the login backend's `username__iexact` lookup assumes.
            models.UniqueConstraint(
                Lower("username"),
                name="user_username_ci_unique",
            ),
        ]

    def __str__(self):
        return self.email or self.username

    def get_full_name(self):
        """`full_name` is the name of record; first_name/last_name are unused.

        AbstractUser's pair assumes a given name and a family name. Ethiopian
        names are a personal name followed by the father's and the
        grandfather's, so there is no surname to put in `last_name` -- splitting
        a typed name across the two columns is a guess, and it is wrong here.
        The name is therefore stored whole and never split.

        Overriding both accessors is what keeps one answer to "what is this
        person called". Without it `send_password_reset_email` greets everyone
        by `first_name`, which is now always empty, and the mail reads "Hello ,".
        """
        return self.full_name or super().get_full_name()

    def get_short_name(self):
        if self.full_name:
            return self.full_name.split(" ")[0]
        return super().get_short_name()

    def clean(self):
        """Undo `AbstractUser.clean()`'s coercion of a missing email to `""`.

        It calls `BaseUserManager.normalize_email`, which opens with
        `email = email or ""` -- so on any ModelForm or admin save it turns our
        None back into an empty string, and the second such user then collides on
        the unique constraint. Re-normalising after super() is what keeps
        "this account has no email" representable.
        """
        super().clean()
        self.email = normalize_email(self.email)

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        self.phone = normalize_phone(self.phone)
        super().save(*args, **kwargs)


class OTPVerification(BaseModel):
    """Tracks a 6-digit OTP verification request for email (or phone).

    Stores a hash of the 6-digit code, destination (email/phone), mode,
    failed attempts, and expiration/resend timestamps.
    """

    destination = models.CharField(max_length=255, db_index=True)
    code_hash = models.CharField(max_length=128)
    mode = models.CharField(max_length=20, default="signup")
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    resend_after = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        verbose_name = "OTP Verification"
        verbose_name_plural = "OTP Verifications"
        indexes = [
            models.Index(
                fields=["destination", "-created_at"],
                name="otp_dest_created_idx",
            ),
        ]

    def __str__(self):
        return f"OTP({self.destination}, mode={self.mode}, used={self.is_used})"
