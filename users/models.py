from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    phone = models.CharField(max_length=30, blank=True)
    telegram_chat_id = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email or self.username
