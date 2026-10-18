# Generated manually for TIN-114 Part 2

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inquiries", "0002_inbox_ordering_and_product_set_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="inquiry",
            name="last_sender_role",
            field=models.CharField(
                blank=True,
                choices=[("buyer", "Buyer"), ("shop", "Shop")],
                db_default=models.Value(""),
                default="",
                max_length=20,
            ),
        ),
    ]
