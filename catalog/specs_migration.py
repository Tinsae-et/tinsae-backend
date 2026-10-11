"""Convert free-text specs from before the schema into schema keys and values.

Each category has a mapper that reads the old keys ("512GB SSD", "Dual
Nano-SIM", "Over-ear Wireless") and proposes schema values (`512gb`, `dual`,
`over_ear`). A proposal is written only if it passes the schema, and old keys
are never removed -- the product page still reads `display`, `chip` and the
rest until those become catalogue data. What cannot be inferred (warranty,
color on most seeded rows) stays missing and shows up in the report.
"""

import re

from .specs import as_text, check_value, matches_condition, size_to_gb


def _text(specs, key):
    value = specs.get(key)
    return str(value).strip().lower() if value not in (None, "") else ""


def _size(text):
    """GB as a number: '512GB SSD' -> 512; '1TB' -> 1024; '512MB' -> 0.5.

    Needs a unit, so a stray number in the text is never taken for a size.
    """
    match = re.search(r"\d+(?:\.\d+)?\s*(?:tb|gb|mb)\b", text, re.IGNORECASE)
    return size_to_gb(match.group()) if match else None


def _number(text):
    """First number in the text, commas dropped: '10,000 mAh' -> 10000."""
    match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    value = float(match.group())
    return int(value) if value.is_integer() else value


def first_match(text, table):
    """The value of the first (pattern, value) row whose regex matches."""
    for pattern, value in table:
        if re.search(pattern, text):
            return value
    return None


PROCESSORS = [
    (r"ultra\s*9", "intel_core_ultra_9"),
    (r"ultra\s*7", "intel_core_ultra_7"),
    (r"ultra\s*5", "intel_core_ultra_5"),
    (r"\bi9\b", "intel_core_i9"),
    (r"\bi7\b", "intel_core_i7"),
    (r"\bi5\b", "intel_core_i5"),
    (r"\bi3\b", "intel_core_i3"),
    (r"ryzen\s*9", "amd_ryzen_9"),
    (r"ryzen\s*7", "amd_ryzen_7"),
    (r"ryzen\s*5", "amd_ryzen_5"),
    (r"ryzen\s*3", "amd_ryzen_3"),
    (r"\bm4\b", "apple_m4"),
    (r"\bm3\b", "apple_m3"),
    (r"\bm2\b", "apple_m2"),
    (r"\bm1\b", "apple_m1"),
    (r"snapdragon\s*x", "snapdragon_x"),
]

LENS_MOUNTS = [
    (r"\brf\b", "canon_rf"),
    (r"\bef\b", "canon_ef"),
    (r"sony\s*e\b|\be-mount\b|\be mount\b", "sony_e"),
    (r"\bz\b|z-mount|z mount", "nikon_z"),
    (r"\bf-mount\b|\bf mount\b", "nikon_f"),
    (r"\bx-mount\b|\bx mount\b|fujifilm\s*x", "fujifilm_x"),
    (r"micro four|\bmft\b|\bm4/3\b", "micro_four_thirds"),
]


def _raw(specs, key):
    value = specs.get(key)
    return str(value).strip() if value not in (None, "") else ""


def display_parts(raw):
    """'6.2″ Dynamic AMOLED 2X 120Hz' -> (6.2, 'Dynamic AMOLED 2X', 120)."""
    size = re.search(r"(\d+(?:\.\d+)?)\s*(?:″|\"|”|-?inch\b|in\b)", raw, re.I)
    refresh = re.search(r"(\d{2,3})\s*hz\b", raw, re.I)
    panel = raw
    for match in (size, refresh):
        if match:
            panel = panel.replace(match.group(0), " ")
    panel = re.sub(r"\s+", " ", panel).strip(" ·,-")
    return (
        _number(size.group(1)) if size else None,
        panel or None,
        int(refresh.group(1)) if refresh else None,
    )


def camera_parts(text):
    """'50MP Triple OIS' / '48MP Main + 12MP + 12MP' -> (50, 'triple')."""
    megapixels = re.findall(r"(\d+(?:\.\d+)?)\s*mp", text)
    setup = first_match(
        text, [(r"quad", "quad"), (r"triple", "triple"), (r"dual", "dual")]
    ) or {1: "single", 2: "dual", 3: "triple", 4: "quad"}.get(len(megapixels))
    return (_number(megapixels[0]) if megapixels else None), setup


def _phones(specs):
    sim = _text(specs, "sim")
    size, _panel, _refresh = display_parts(_raw(specs, "display"))
    main_camera, _setup = camera_parts(_text(specs, "camera"))
    capacity = re.search(r"(\d[\d,]*)\s*mah", _text(specs, "battery"))
    return {
        # Set before the fields it gates: storage/RAM/camera are smart-only, so
        # a legacy listing needs a phone type before they can be mapped. A tiny
        # screen is the old keypad signature.
        "phone_type": (
            "feature_phone" if size is not None and size < 3 else "smartphone"
        ),
        "screen_size": size,
        "main_camera": main_camera,
        "battery_capacity": (
            int(capacity.group(1).replace(",", "")) if capacity else None
        ),
        "storage": _size(_text(specs, "storage")),
        "ram": _size(_text(specs, "ram")),
        "sim": (
            "sim_esim"
            if "esim" in sim and ("nano" in sim or "dual" in sim)
            else (
                "dual"
                if "dual" in sim
                else "esim" if "esim" in sim else "single" if sim else None
            )
        ),
        "network": (
            ("5g" if _text(specs, "n5g").startswith("yes") else "4g")
            if "n5g" in specs
            else None
        ),
    }


def storage_type_from_text(text):
    """'512GB SSD' -> 'ssd'; '256GB SSD + 1TB HDD' -> 'ssd_hdd'."""
    has_ssd = bool(re.search(r"ssd|nvme", text))
    has_hdd = bool(re.search(r"hdd|hard", text))
    if has_ssd and has_hdd:
        return "ssd_hdd"
    if re.search(r"emmc", text):
        return "emmc"
    return "ssd" if has_ssd else "hdd" if has_hdd else None


def _laptops(specs):
    size, _panel, _refresh = display_parts(_raw(specs, "display"))
    os_text = _text(specs, "os")
    return {
        "storage_type": storage_type_from_text(_text(specs, "storage")),
        "storage": _size(_text(specs, "storage")),
        "ram": _size(_text(specs, "ram")),
        "processor": first_match(_text(specs, "processor"), PROCESSORS),
        "screen_size": size if size and 10 <= size <= 20 else None,
        "os": first_match(
            os_text,
            [
                (r"windows", "windows"),
                (r"macos|mac os", "macos"),
                (r"chrome", "chromeos"),
                (r"linux|ubuntu", "linux"),
            ],
        ),
        "gpu": str(specs["gpu"])[:120] if specs.get("gpu") else None,
    }


def _tablets(specs):
    connectivity = _text(specs, "connectivity")
    return {
        "storage": _size(_text(specs, "storage")),
        "ram": _size(_text(specs, "ram")),
        "connectivity": (
            "wifi_cellular"
            if re.search(r"cellular|5g|lte|4g", connectivity)
            else "wifi" if connectivity else None
        ),
    }


def _audio(specs):
    kind = _text(specs, "type")
    anc = _text(specs, "anc")
    water = re.search(r"ipx?\d", _text(specs, "water_resistance"))
    hours = re.search(r"(\d+(?:\.\d+)?)\s*h", _text(specs, "battery"))
    return {
        "audio_type": first_match(
            kind,
            [
                (r"soundbar", "soundbar"),
                (r"speaker", "speaker"),
                (r"over", "over_ear"),
                (r"on-ear|on ear", "on_ear"),
                (r"in-ear|in ear|earbud|true wireless", "earbuds"),
            ],
        ),
        "connectivity": "bluetooth" if specs.get("bluetooth") else None,
        "noise_cancellation": (
            "no" if anc.startswith("no") else "yes" if anc else None
        ),
        "water_resistance": water.group().lower() if water else None,
        # "30h with ANC (40h without)": the first figure, as advertised.
        "battery_life": _number(hours.group(1)) if hours else None,
    }


def _cameras(specs):
    mount = first_match(_text(specs, "lens_mount"), LENS_MOUNTS)
    kit = _text(specs, "kit")
    return {
        "camera_type": (
            ("dslr" if mount in ("canon_ef", "nikon_f") else "mirrorless")
            if mount
            else None
        ),
        "lens_mount": mount,
        "kit": (
            "body_only"
            if "body" in kit
            else "with_lens" if kit or specs.get("lens") else None
        ),
        "megapixels": _number(_text(specs, "resolution")),
        # "4K 60p 10-bit", "6.2K 30p", "8.3K 60p N-RAW": the headline format.
        "video_resolution": first_match(
            _text(specs, "video"),
            [
                (r"\b([6-9](\.\d)?k)\b", "6k"),
                (r"\b(4k|5\.\dk)\b", "4k"),
                (r"1080|full hd", "fhd"),
            ],
        ),
    }


def _accessories(specs):
    kind = _text(specs, "type")
    accessory_type = first_match(
        kind,
        [
            (r"power\s*bank|powerbank", "power_bank"),
            (r"charg|plug|adapter", "charger"),
            (r"cable", "cable"),
            (r"screen protector|tempered glass", "screen_protector"),
            (r"\bcase\b|cover", "case"),
            (r"mouse|keyboard", "mouse_keyboard"),
            (r"ssd|drive|storage", "external_storage"),
        ],
    ) or ("other" if kind else None)
    port = _text(specs, "port")
    return {
        "accessory_type": accessory_type,
        "compatible_with": first_match(
            _text(specs, "compatibility"),
            [
                (r"universal", "universal"),
                (r"iphone|magsafe|lightning", "iphone"),
                (r"samsung|galaxy", "samsung_galaxy"),
                (r"laptop|macbook", "laptop_usb_c"),
                (r"android|usb-c|type-c", "android_usb_c"),
            ],
        ),
        "connector": first_match(
            port,
            [
                (r"usb-c|type-c|usb c", "usb_c"),
                (r"lightning", "lightning"),
                (r"micro", "micro_usb"),
                (r"usb-a|usb a", "usb_a"),
            ],
        ),
        "output_power": _number(_text(specs, "power")) if specs.get("power") else None,
        "battery_capacity": (
            _number(_text(specs, "capacity"))
            if "mah" in _text(specs, "capacity")
            else None
        ),
    }


MAPPERS = {
    "phones": _phones,
    "laptops": _laptops,
    "tablets": _tablets,
    "audio": _audio,
    "cameras": _cameras,
    "accessories": _accessories,
}


def migrate_product_specs(category_slug: str, schema: dict, specs: dict, condition):
    """New specs for one product: old keys kept, valid schema values filled in.

    A key already holding a valid schema value is left alone, so re-running is
    safe and a merchant's own later edit is never overwritten.
    """
    mapper = MAPPERS.get(category_slug)
    if mapper is None:
        return dict(specs)
    proposed = {k: v for k, v in mapper(specs).items() if v not in (None, "")}
    result = dict(specs)
    fields = {f["key"]: f for f in schema["fields"]}

    # Type fields first: other fields' `show_if` reads them.
    for key in sorted(proposed, key=lambda k: not k.endswith("_type")):
        field = fields.get(key)
        if field is None:
            continue
        current = result.get(key)
        if current not in (None, "") and check_value(field, current) is None:
            continue
        values = {**result, **proposed, "condition": condition}
        if not matches_condition(field["show_if"], values):
            continue
        value = as_text(field, proposed[key])
        if check_value(field, value) is None:
            result[key] = value
    return result
