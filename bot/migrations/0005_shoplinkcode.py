import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import bot.models


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0004_confirmation_response_tracking"),
        ("shops", "0002_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ShopLinkCode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(blank=True, max_length=16, unique=True)),
                (
                    "expires_at",
                    models.DateTimeField(default=bot.models.default_link_code_expiry),
                ),
                (
                    "used_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "When a chat spent this code. NULL means it is still live."
                        ),
                        null=True,
                    ),
                ),
                (
                    "used_by_chat_id",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Which chat spent it — kept for audit after a re-link."
                        ),
                        max_length=50,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="issued_link_codes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="link_codes",
                        to="shops.shop",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "abstract": False,
            },
        ),
    ]
