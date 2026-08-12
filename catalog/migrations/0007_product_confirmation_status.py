from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_alter_productimage_image_alter_productimage_product"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="confirmation_status",
            field=models.CharField(
                choices=[("fresh", "Fresh"), ("stale", "Stale")],
                default="fresh",
                max_length=20,
            ),
        ),
    ]
