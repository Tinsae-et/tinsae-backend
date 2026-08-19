from django.db import migrations, models


def denied_to_sold_out(apps, schema_editor):
    ConfirmationRequest = apps.get_model("bot", "ConfirmationRequest")
    ConfirmationRequest.objects.filter(status="denied").update(status="sold_out")


def sold_out_to_denied(apps, schema_editor):
    ConfirmationRequest = apps.get_model("bot", "ConfirmationRequest")
    ConfirmationRequest.objects.filter(status="sold_out").update(status="denied")


def backfill_responded_at(apps, schema_editor):
    """`updated_at` is the closest thing we have to a response time for rows
    written before this column existed: apply_confirmation was the only writer of
    a resolved status, and it always saved `updated_at` alongside it.
    """
    ConfirmationRequest = apps.get_model("bot", "ConfirmationRequest")
    ConfirmationRequest.objects.filter(
        status__in=["confirmed", "sold_out"], responded_at__isnull=True
    ).update(responded_at=models.F("updated_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0003_unique_pending_confirmation"),
    ]

    operations = [
        migrations.AddField(
            model_name="confirmationrequest",
            name="responded_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When the merchant answered. Stays NULL for expired requests "
                    "— an expiry is the absence of an answer, not an answer."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="confirmationrequest",
            name="channel",
            field=models.CharField(default="telegram", max_length=20),
        ),
        migrations.AlterField(
            model_name="confirmationrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("confirmed", "Confirmed"),
                    ("sold_out", "Sold out"),
                    ("expired", "Expired"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.RunPython(denied_to_sold_out, sold_out_to_denied),
        migrations.RunPython(backfill_responded_at, migrations.RunPython.noop),
    ]
