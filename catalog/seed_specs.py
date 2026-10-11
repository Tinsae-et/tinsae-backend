"""Schema-valid specs for seeded products.

Seeds describe products loosely -- old free-text specs, a name like "MacBook
Pro 14 M3 16GB 512GB Space Black", a colour word. `seed_specs()` turns that
into specs that pass the category's schema, in three passes:

1. convert old free-text keys with the `migrate_specs` mappers;
2. read what the name and colour text say (storage, RAM, colour, type...);
3. fill every still-missing required field deterministically -- warranty from
   the condition, battery health / cycles / shutter count / mileage for used
   items, and otherwise a stable pick seeded by the product, so reseeding is
   repeatable.

Only keys the schema defines are kept: the seed data still describes products
the old way ("chip", "year", "weight"...), and those details are not part of
the listing any more.

The result is validated before it is returned: a seed that cannot produce a
valid listing fails loudly instead of writing data the form would reject.
"""

import random
import re

from .models import Category, ProductCondition
from .specs import (
    as_text,
    category_schema,
    check_value,
    matches_condition,
    normalize_specs,
    size_to_gb,
    validate_specs,
)
from .specs_migration import (
    PROCESSORS,
    first_match,
    migrate_product_specs,
    storage_type_from_text,
)
from .taxonomy import ATTRIBUTES, PRODUCT_TYPES
from .taxonomy.sync import sync_taxonomy

COLORS = [
    (r"natural titanium|titanium", "natural_titanium"),
    (r"black|midnight|graphite|obsidian|onyx", "black"),
    (r"space gr[ae]y", "space_gray"),
    (r"gr[ae]y|charcoal|slate", "gray"),
    (r"white|starlight|chalk|cream", "white"),
    (r"silver|platinum", "silver"),
    (r"gold|champagne", "gold"),
    (r"blue|navy|ocean|sky", "blue"),
    (r"green|lime|mint|olive", "green"),
    (r"purple|violet|lavender", "purple"),
    (r"pink|rose", "pink"),
    (r"red|crimson", "red"),
    (r"yellow|lemon|amber", "yellow"),
    (r"orange", "orange"),
    (r"brown|bronze", "brown"),
]

# Read from the name when the old specs did not already provide them.
NAME_RULES = {
    "audio_type": [
        (r"soundbar", "soundbar"),
        (r"speaker|boombox|flip|charge|motion", "speaker"),
        (r"earbud|airpods(?! max)|buds|wf-|in-ear", "earbuds"),
        (r"over-ear|airpods max|wh-|headphone", "over_ear"),
        (r"on-ear", "on_ear"),
    ],
    "camera_type": [
        (r"gopro|action", "action"),
        (r"drone|mavic|mini \d", "drone"),
        (r"dslr|eos \d{1,4}d|\bd\d{3,4}\b", "dslr"),
        (r"mirrorless|alpha|eos r|\bz ?\d|x-t|x-s", "mirrorless"),
        (r"powershot|coolpix|rx100|compact", "compact"),
    ],
    "kit": [(r"body", "body_only"), (r"with \d|kit|\d+-\d+mm", "with_lens")],
    "accessory_type": [
        (r"power\s*bank|powercore", "power_bank"),
        (r"charg|adapter|plug", "charger"),
        (r"cable", "cable"),
        (r"screen protector|tempered", "screen_protector"),
        (r"\bcase\b|cover", "case"),
        (r"mouse|keyboard", "mouse_keyboard"),
        (r"\bssd\b|drive", "external_storage"),
    ],
    "lens_mount": [
        (r"eos r|canon.*\br\d", "canon_rf"),
        (r"canon", "canon_ef"),
        (r"sony|alpha", "sony_e"),
        (r"nikon z|\bz ?\d", "nikon_z"),
        (r"nikon", "nikon_f"),
        (r"fujifilm|x-t\d|x-s\d", "fujifilm_x"),
        (r"panasonic|olympus|om system", "micro_four_thirds"),
    ],
    # Order matters: a workstation's "RTX A2000" must not read as gaming.
    "laptop_type": [
        (r"workstation|precision|zbook|quadro|rtx a\d", "workstation"),
        (r"2-in-1|x360|yoga|flip|surface pro|convertible", "two_in_one"),
        (r"chromebook", "chromebook"),
        (r"gaming|legion|rog|tuf|omen|predator|nitro|alienware|victus|rtx", "gaming"),
        (
            r"thinkpad|latitude|elitebook|probook|vostro|expertbook|travelmate",
            "business",
        ),
        (r"macbook|xps|zenbook|spectre|swift|gram|envy|air", "thin_light"),
    ],
    "noise_cancellation": [(r"noise cancel|anc\b", "yes")],
    "sim": [(r"dual sim", "dual")],
    "network": [(r"\b5g\b", "5g")],
}

# The only optional fields a seed fills at random, because any value is
# plausible for them. Everything else stays empty unless the name or old specs
# say so: a random pick would give a Canon a Sony mount.
MAY_GUESS = {"water_resistance", "compatible_with", "connector"}

# What a seed assumes when the name is silent, instead of a random pick.
INVENT_DEFAULTS = {
    "storage_type": "ssd",
    "network": "4g",
    "carrier_lock": "unlocked",
}

# Keyed by the plain value: enum members hash by name, so a
# `ProductCondition.USED` key would never match the string "used".
WARRANTY_BY_CONDITION = {
    ProductCondition.NEW.value: "brand_1y",
    ProductCondition.REFURBISHED.value: "shop_6m",
    ProductCondition.USED.value: "shop_3m",
}

# Sizes a seed picks from when the name does not state one: mainstream for the
# type, so an iPhone Pro whose name omits RAM never gets a budget 2GB.
INVENT_SIZES_GB = {
    ("smartphone", "storage"): [64, 128, 256, 512],
    ("smartphone", "ram"): [4, 6, 8, 12],
    ("tablet", "storage"): [64, 128, 256],
    ("tablet", "ram"): [4, 6, 8],
    ("laptop", "storage"): [256, 512, 1024],
    ("laptop", "secondary_storage"): [500, 1024, 2048],
    ("laptop", "ram"): [8, 16, 32],
    ("accessory", "storage"): [64, 128, 256, 512, 1024],
}

# Plausible ranges for numbers a seed has to invent.
NUMBER_RANGES = {
    "battery_health": (78, 99),
    "battery_cycles": (40, 650),
    "shutter_count": (1500, 45000),
    "screen_size": (13, 16),
    "output_power": (10, 65),
    "battery_capacity": (5000, 20000),
    "power_output": (5, 60),
    "motor_power": (500, 4000),
    "megapixels": (12, 45),
    "mileage": (12000, 180000),
}

_schemas: dict[int, dict | None] = {}


def _schema_for(category: Category) -> dict | None:
    if category.pk not in _schemas:
        _schemas[category.pk] = category_schema(category)
    return _schemas[category.pk]


def ensure_taxonomy() -> None:
    """Link the categories to their product types before specs are built.

    Seeded specs are held to the same schemas as merchant listings, so the
    taxonomy has to be in the database first. Re-running the sync is safe.
    Call it after the seed's categories exist, so they get linked.
    """
    sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
    _schemas.clear()


def _sizes(text: str) -> list[float]:
    """Every '256GB' / '1 TB' in the text, in GB."""
    return [
        size_to_gb(m) for m in re.findall(r"\d+(?:\.\d+)?\s*(?:gb|tb)\b", text, re.I)
    ]


def _from_name(field: dict, text: str) -> object | None:
    key = field["key"]
    if key in ("storage", "ram"):
        mentioned = _sizes(text)
        # One size in a name ("iPhone 13 128GB") is the storage; RAM needs two.
        # Storage is the larger number, RAM the smaller (and never above 64GB).
        if key == "storage":
            return max(mentioned) if mentioned else None
        small = [gb for gb in mentioned if gb <= 64]
        return min(small) if len(mentioned) >= 2 and small else None
    if key == "color":
        return first_match(text, COLORS)
    if key == "processor":
        return first_match(text, PROCESSORS)
    if key == "storage_type":
        return storage_type_from_text(text)
    if key == "connectivity":
        # First match this type offers: a speaker named "Wi-Fi & Bluetooth"
        # is bluetooth, a tablet named "Wi-Fi" is wifi.
        allowed = {o["value"] for o in field["options"] or []}
        for pattern, value in [
            (r"cellular|\b5g\b|lte", "wifi_cellular"),
            (r"wi-?fi", "wifi"),
            (r"bluetooth|wireless|airpods|buds", "bluetooth"),
            (r"wired", "wired"),
        ]:
            if value in allowed and re.search(pattern, text):
                return value
        return None
    if key == "battery_capacity":
        match = re.search(r"(\d[\d,]*)\s*mah", text)
        return int(match.group(1).replace(",", "")) if match else None
    if key in NAME_RULES:
        return first_match(text, NAME_RULES[key])
    return None


def _invent(
    field: dict, condition: str, rng: random.Random, product_type: str
) -> object:
    key = field["key"]
    if key == "warranty":
        return WARRANTY_BY_CONDITION.get(condition, "shop_3m")
    sizes = INVENT_SIZES_GB.get((product_type, key))
    if sizes:
        return rng.choice(sizes)
    if field["type"] == "number":
        low, high = NUMBER_RANGES.get(key, (field["min"] or 1, field["max"] or 100))
        return rng.randint(int(low), int(high))
    if key in INVENT_DEFAULTS:
        return INVENT_DEFAULTS[key]
    if field["type"] == "text":
        # Typed fields store what a person would type: a suggestion's label.
        labels = [o["label"] for o in field["options"] or []]
        return rng.choice(labels) if labels else "Integrated"
    # "Other" is an owner's escape hatch, never a seed's guess.
    choices = [o["value"] for o in field["options"] if o["value"] != "other"]
    return rng.choice(choices)


def seed_specs(
    category: Category,
    specs: dict | None,
    *,
    text: str,
    condition: str,
    seed: str,
    optional_rate: float = 0.5,
) -> dict:
    """Specs for one seeded product that pass its category's schema.

    `text` is everything that describes the product in words (name, colour,
    description); `seed` makes the invented values stable per product.
    `optional_rate` is how often an optional field the name did not settle is
    filled anyway, so seeded listings vary the way real ones do.
    """
    specs = dict(specs or {})
    schema = _schema_for(category)
    if schema is None:
        return specs
    # Callers pass either "used" or ProductCondition.USED; compare as text.
    condition = str(condition)

    rng = random.Random(seed)
    text = text.lower()
    result = migrate_product_specs(category.slug, schema, specs, condition)
    fields = schema["fields"]

    # Schema order puts each type's `*_type` field first, so a field's
    # `show_if` always sees the value it depends on.
    for field in fields:
        key = field["key"]
        values = {**result, "condition": condition}
        if not matches_condition(field["show_if"], values):
            result.pop(key, None)
            continue
        # A keypad phone is 2G, whatever the old free-text specs implied.
        if key == "network" and result.get("phone_type") == "feature_phone":
            result[key] = "2g"
            continue
        current = result.get(key)
        if current not in (None, "") and check_value(field, current) is None:
            continue
        result.pop(key, None)

        guess = _from_name(field, text)
        if guess is not None:
            guess = as_text(field, guess)
            # A multiple field stores a list, even of one guessed value.
            if field.get("multiple"):
                guess = [guess]
        if guess is not None and check_value(field, guess) is None:
            result[key] = guess
            continue

        required = field["required"] or (
            field["required_if"] is not None
            and matches_condition(field["required_if"], values)
        )
        # A few harmless optional fields get filled some of the time, so
        # seeded listings vary the way real ones do.
        optional_pick = key in MAY_GUESS and rng.random() < optional_rate
        if required or optional_pick:
            invented = _invent(field, condition, rng, schema["product_type"])
            invented = as_text(field, invented)
            result[key] = [invented] if field.get("multiple") else invented

    # Old free-text keys have given what they could; the rest is dropped.
    defined = {f["key"] for f in fields}
    result = {k: v for k, v in result.items() if k in defined}

    # Stored exactly as a merchant's save would store it.
    result = normalize_specs(schema, result)
    errors = validate_specs(schema, result, condition=condition)
    if errors:
        raise ValueError(f"Seed specs for {seed!r} fail the schema: {errors}")
    return result
