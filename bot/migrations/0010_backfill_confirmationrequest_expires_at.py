from datetime import timedelta

from django.conf import settings
from django.db import migrations


def backfill_expires_at(apps, schema_editor):
    ConfirmationRequest = apps.get_model('bot', 'ConfirmationRequest')
    hidden_days = getattr(settings, 'CONFIRMATION_HIDDEN_AFTER_DAYS', 14)
    for req in ConfirmationRequest.objects.filter(status='pending', expires_at__isnull=True):
        if req.created_at:
            req.expires_at = req.created_at + timedelta(days=hidden_days)
            req.save(update_fields=['expires_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0009_confirmationrequest_expires_at'),
    ]

    operations = [
        migrations.RunPython(backfill_expires_at, migrations.RunPython.noop),
    ]
