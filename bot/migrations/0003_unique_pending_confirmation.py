from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0002_alter_confirmationrequest_status"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="confirmationrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("product",),
                name="unique_pending_confirmation_per_product",
            ),
        ),
    ]
