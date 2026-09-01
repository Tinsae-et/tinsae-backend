from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Enable pg_trgm, which `catalog.services.SearchProduct` needs.

    Replaces `0010_auto_20260826_1711`, which branched off 0009 alongside
    `0010_alter_product_confirmation_status` and left the graph with two leaf
    nodes -- `migrate` refuses to run at all in that state. Reparented onto the
    tip instead of merged: the operation is a bare `CREATE EXTENSION IF NOT
    EXISTS`, so re-running it on a database that already applied the old
    migration is a no-op.
    """

    dependencies = [
        ("catalog", "0011_product_condition"),
    ]

    operations = [
        TrigramExtension(),
    ]
