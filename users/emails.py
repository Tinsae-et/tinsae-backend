from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

SUBJECT = "Reset your Tinsae password"

BODY = """Hello {name},

We received a request to reset the password for your Tinsae account.

Open this link to choose a new one:

{url}

The link is valid for {hours} hours and can only be used once. If you did not
ask for a password reset you can ignore this message -- your password has not
changed.

-- Tinsae
"""


def build_password_reset_url(user):
    """The link that goes in the email.

    It points at the frontend, not at Django: the reset form is a Next.js page
    that collects the new password and POSTs it back to
    `/api/v1/auth/password/reset/confirm/`. Django never renders a page here.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    base = settings.FRONTEND_URL.rstrip("/")
    return f"{base}/reset-password/{uid}/{token}/"


def send_password_reset_email(user):
    """Send the reset link. Returns False if the account has no email.

    Email is optional on `User`, so a merchant who registered without one has no
    self-service reset path and is an admin-assisted reset instead. The caller
    must not surface the difference -- see `PasswordResetRequestView`.
    """
    if not user.email:
        return False

    send_mail(
        subject=SUBJECT,
        message=BODY.format(
            name=user.get_short_name() or user.username,
            url=build_password_reset_url(user),
            hours=settings.PASSWORD_RESET_TIMEOUT // 3600,
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return True


OTP_SUBJECT = "Your Tinsae verification code"

OTP_BODY = """Hello,

Your verification code is:

{code}

This code is valid for 10 minutes and can only be used once. If you did not request this code, you can safely ignore this email.

-- Tinsae
"""


def send_otp_email(email, code):
    """Send a 6-digit OTP code to the given email address."""
    if not email:
        return False

    send_mail(
        subject=OTP_SUBJECT,
        message=OTP_BODY.format(code=code),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
    return True
