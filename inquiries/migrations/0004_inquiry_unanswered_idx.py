# Generated manually for TIN-114 Part 2 (P2.2: safe concurrent index on live table)

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("inquiries", "0003_inquiry_last_sender_role"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="inquiry",
            index=models.Index(
                fields=["last_message_at"],
                condition=models.Q(status="open", last_sender_role="buyer"),
                name="inquiry_unanswered_idx",
            ),
        ),
    ]
