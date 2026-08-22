from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0008_alter_product_slug"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="product",
            index=models.Index(
                fields=["last_confirmed_at"], name="product_last_confirmed_idx"
            ),
        ),
    ]
