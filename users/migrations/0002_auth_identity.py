"""Make username, email and phone all usable as unique login identifiers.

Hand-ordered, and the order is load-bearing:

1-2. Widen to NULL first. Both columns are NOT NULL today, so nothing can write
     the NULLs that step 3 needs. Phone keeps max_length=30 here -- narrowing it
     before normalisation could truncate a stored value.
  3. Normalise: "" -> NULL, emails lowercased, phones to E.164. Then fail loudly,
     naming the rows, if real duplicates remain. A bare IntegrityError from
     Postgres would only name an index.
4-5. Add the unique constraints, and narrow phone now that step 3 guarantees
     every surviving value is at most 13 characters.
  6. Case-insensitive uniqueness on username, which the login backend's
     `username__iexact` lookup assumes.

Not reversible past step 3: reversing would have to put NULLs back into NOT NULL
columns. Restore from a backup instead.
"""

import re

from django.db import migrations, models
from django.db.models.functions import Lower

_SEPARATORS = re.compile(r"[\s\-().]")
_ETHIOPIAN_MOBILE = re.compile(r"^[79]\d{8}$")


def _phone(value):
    """Same rules as users.validators.normalize_phone, inlined.

    Migrations must not import live application code: a later change to the
    validator would silently rewrite what this historical migration did.
    Unparseable numbers become NULL rather than raising -- this is a cleanup
    pass over data that predates the rule, not an input path.
    """
    if not value:
        return None
    cleaned = _SEPARATORS.sub("", str(value)).strip()
    if not cleaned:
        return None
    for prefix, cut in (("+251", 4), ("251", 3), ("0", 1)):
        if cleaned.startswith(prefix):
            cleaned = cleaned[cut:]
            break
    if not _ETHIOPIAN_MOBILE.match(cleaned):
        return None
    return f"+251{cleaned}"


def _email(value):
    if not value:
        return None
    return str(value).strip().lower() or None


def normalize_identity(apps, schema_editor):
    User = apps.get_model("users", "User")

    for user in User.objects.all().iterator():
        email = _email(user.email)
        phone = _phone(user.phone)
        if email != user.email or phone != user.phone:
            user.email = email
            user.phone = phone
            user.save(update_fields=["email", "phone"])

    problems = []
    for field in ("email", "phone", "username"):
        seen = {}
        for pk, value in User.objects.values_list("pk", field):
            key = value.lower() if field == "username" else value
            if key is None:
                continue
            seen.setdefault(key, []).append(pk)
        for value, pks in seen.items():
            if len(pks) > 1:
                problems.append(f"  {field}={value!r} on user ids {sorted(pks)}")

    if problems:
        raise RuntimeError(
            "Cannot apply users.0002: these values must be unique but are "
            "duplicated. Resolve them and re-run the migration.\n" + "\n".join(problems)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(
                blank=True, max_length=254, null=True, verbose_name="email address"
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.RunPython(normalize_identity, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(
                blank=True,
                max_length=254,
                null=True,
                unique=True,
                verbose_name="email address",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.CharField(blank=True, max_length=20, null=True, unique=True),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("username"), name="user_username_ci_unique"
            ),
        ),
    ]
