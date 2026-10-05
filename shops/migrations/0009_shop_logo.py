from django.db import migrations, models

from catalog.image_utils import validate_image_file


class Migration(migrations.Migration):
    dependencies = [
        ("shops", "0008_shop_address_categories_shopphoto"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="logo",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="shops/logos/",
                validators=[validate_image_file],
            ),
        ),
    ]
