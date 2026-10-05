from django.db import migrations, models
import django.db.models.deletion
from catalog.image_utils import validate_image_file


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0019_producttypeattribute_recommended_and_more"),
        ("shops", "0007_shop_blocked_at_shop_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="address",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="shop",
            name="categories",
            field=models.ManyToManyField(
                blank=True, related_name="shops", to="catalog.category"
            ),
        ),
        migrations.CreateModel(
            name="ShopPhoto",
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
                (
                    "image",
                    models.ImageField(
                        upload_to="shops/photos/", validators=[validate_image_file]
                    ),
                ),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("is_cover", models.BooleanField(default=False)),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="shops.shop",
                    ),
                ),
            ],
            options={"ordering": ["-is_cover", "display_order", "created_at"]},
        ),
    ]
