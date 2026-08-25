from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from .validators import normalize_email, normalize_phone


class User(AbstractUser):
    """A merchant account.

    `username` remains `USERNAME_FIELD`, but username, email and phone are all
    unique and any of the three can be used to log in -- see
    `users.backends.MultiIdentifierBackend`.

    `email` and `phone` are `null=True`, not merely `blank=True`. Both carry a
    unique constraint, and Postgres treats NULLs as distinct while two empty
    strings collide; stored as `""`, exactly one user in the table could have no
    phone. Neither is required by the database -- registration requires a phone
    at the serializer layer, which is where product policy belongs, and leaves
    email optional because a large share of Ethiopian merchants do not have one.
    """

    email = models.EmailField(
        "email address", max_length=254, unique=True, null=True, blank=True
    )
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    telegram_chat_id = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["email"]
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
