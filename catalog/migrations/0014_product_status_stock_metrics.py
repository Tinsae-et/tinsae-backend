from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0013_searchlog_product_product_name_trgm_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="status",
            field=models.CharField(
                choices=[("live", "Live"), ("draft", "Draft"), ("sold", "Sold")],
                default="live",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="stock",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="product",
            name="views",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="product",
            name="inquiries",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
