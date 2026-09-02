from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_alter_user_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="OTPVerification",
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
                ("destination", models.CharField(db_index=True, max_length=255)),
                ("code_hash", models.CharField(max_length=128)),
                ("mode", models.CharField(default="signup", max_length=20)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("expires_at", models.DateTimeField()),
                ("resend_after", models.DateTimeField()),
                ("is_used", models.BooleanField(default=False)),
            ],
            options={
                "verbose_name": "OTP Verification",
                "verbose_name_plural": "OTP Verifications",
                "ordering": ["-created_at"],
                "abstract": False,
                "indexes": [
                    models.Index(
                        fields=["destination", "-created_at"],
                        name="otp_dest_created_idx",
                    )
                ],
            },
        ),
    ]
