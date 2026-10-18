"""Notification delivery channels registry."""

from . import email, in_app, telegram

CHANNELS = {
    "in_app": in_app,
    "telegram": telegram,
    "email": email,
}
