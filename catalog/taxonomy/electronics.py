"""Electronics: the six live categories and the specs their listings carry.

`ATTRIBUTES` is the global list: each key is defined once. `PRODUCT_TYPES`
picks attributes per type, narrows enum options with `options`, and sets the
rules. What kind of input each spec gets follows what the spec really is:

- enum: a genuinely closed set, or one other fields depend on (SIM, network,
  storage type, audio/camera/accessory type). Free text there would break
  the form's `show_if` rules or split one answer into five spellings.
- number: a measurable quantity, stored in its base unit (storage and RAM in
  GB, screen in inches). Anything can be typed -- 480GB, 1.5TB, 20GB -- and it
  still sorts, compares and filters.
- text: pick or type (color, processor, OS, warranty, compatibility...).
  `options` on a text attribute are suggestions offered while typing, not a
  limit; a typed value matching one is stored as that suggestion
  (catalog/specs.py normalize_specs), so filters still group them.
"""


def _opts(*pairs):
    return [{"value": value, "label": label} for value, label in pairs]


# Storage and RAM were enums of sizes ("256gb"). `convert_from` tells the sync
# to convert stored values to numbers in GB once, in the same transaction; see
# catalog/taxonomy/sync.py `CONVERTERS`.
SIZE_FROM_ENUM = {"type": "enum", "using": "size_gb"}

ATTRIBUTES = {
    # --- shared across types ---
    # Pick or type: the suggestions cover the common colours, and the
    # manufacturer's own name ("Titanium Blue") can be typed. A typed value
    # that matches a suggestion is stored as the suggestion ("black" ->
    # "Black"; see catalog/specs.py normalize_specs), so filters still group.
    "color": {
        "label": "Color",
        "type": "text",
        "options": _opts(
            ("black", "Black"),
            ("white", "White"),
            ("silver", "Silver"),
            ("gray", "Gray"),
            ("space_gray", "Space Gray"),
            ("gold", "Gold"),
            ("rose_gold", "Rose Gold"),
            ("blue", "Blue"),
            ("green", "Green"),
            ("purple", "Purple"),
            ("pink", "Pink"),
            ("red", "Red"),
            ("orange", "Orange"),
            ("yellow", "Yellow"),
            ("brown", "Brown"),
            ("midnight", "Midnight"),
            ("starlight", "Starlight"),
            ("natural_titanium", "Natural Titanium"),
        ),
    },
    # Pick or type: "Shop · 18 months" or "Brand 2y + shop 6m" are real too.
    "warranty": {
        "label": "Warranty",
        "type": "text",
        "options": _opts(
            ("none", "No warranty"),
            ("shop_1m", "Shop · 1 month"),
            ("shop_3m", "Shop · 3 months"),
            ("shop_6m", "Shop · 6 months"),
            ("shop_1y", "Shop · 1 year"),
            ("shop_2y", "Shop · 2 years"),
            ("brand_1y", "Brand · 1 year"),
            ("brand_2y", "Brand · 2 years"),
            ("brand_3y", "Brand · 3 years"),
        ),
    },
    "storage": {
        "label": "Storage",
        "type": "number",
        "unit": "GB",
        "min": 1,
        "max": 65536,
        "convert_from": SIZE_FROM_ENUM,
    },
    "storage_type": {
        "label": "Storage type",
        "type": "enum",
        "options": _opts(
            ("ssd", "SSD"),
            ("hdd", "HDD"),
            ("emmc", "eMMC"),
            ("ssd_hdd", "SSD + HDD"),
        ),
    },
    "secondary_storage": {
        "label": "HDD size",
        "type": "number",
        "unit": "GB",
        "min": 1,
        "max": 65536,
        "convert_from": SIZE_FROM_ENUM,
    },
    "ram": {
        "label": "RAM",
        "type": "number",
        "unit": "GB",
        # 0.5 for the 512MB feature-class phones still sold.
        "min": 0.5,
        "max": 512,
        "convert_from": SIZE_FROM_ENUM,
    },
    "connectivity": {
        "label": "Connectivity",
        "type": "enum",
        "options": _opts(
            ("wifi", "Wi-Fi"),
            ("wifi_cellular", "Wi-Fi + Cellular"),
            ("bluetooth", "Bluetooth"),
            ("wired", "Wired"),
            ("bluetooth_wired", "Bluetooth + wired"),
        ),
    },
    "battery_health": {
        "label": "Battery health",
        "type": "number",
        "unit": "%",
        "min": 0,
        "max": 100,
    },
    "screen_size": {
        "label": "Screen size",
        "type": "number",
        "unit": "in",
        "min": 1,
        "max": 100,
    },
    "touchscreen": {
        "label": "Touchscreen",
        "type": "enum",
        "options": _opts(("yes", "Yes"), ("no", "No")),
    },
    # --- phones & tablets ---
    "sim": {
        "label": "SIM",
        "type": "enum",
        "options": _opts(
            ("single", "Single"),
            ("dual", "Dual"),
            ("esim", "eSIM only"),
            ("sim_esim", "SIM + eSIM"),
        ),
    },
    "network": {
        "label": "Network",
        "type": "enum",
        "options": _opts(("3g", "3G"), ("4g", "4G"), ("5g", "5G")),
    },
    # --- laptops ---
    # What the laptop is for -- the first thing buyers narrow by. Pick or type
    # ("Creator", "Rugged"): nothing in the form depends on its value.
    "laptop_type": {
        "label": "Laptop type",
        "type": "text",
        "options": _opts(
            ("gaming", "Gaming"),
            ("business", "Business"),
            ("workstation", "Workstation"),
            ("thin_light", "Thin & light"),
            ("two_in_one", "2-in-1"),
            ("chromebook", "Chromebook"),
            ("everyday", "Everyday / student"),
        ),
    },
    "processor": {
        "label": "Processor",
        "type": "text",
        "options": _opts(
            ("intel_celeron", "Intel Celeron"),
            ("intel_pentium", "Intel Pentium"),
            ("intel_core_i3", "Intel Core i3"),
            ("intel_core_i5", "Intel Core i5"),
            ("intel_core_i7", "Intel Core i7"),
            ("intel_core_i9", "Intel Core i9"),
            ("intel_core_ultra_5", "Intel Core Ultra 5"),
            ("intel_core_ultra_7", "Intel Core Ultra 7"),
            ("intel_core_ultra_9", "Intel Core Ultra 9"),
            ("amd_athlon", "AMD Athlon"),
            ("amd_ryzen_3", "AMD Ryzen 3"),
            ("amd_ryzen_5", "AMD Ryzen 5"),
            ("amd_ryzen_7", "AMD Ryzen 7"),
            ("amd_ryzen_9", "AMD Ryzen 9"),
            ("apple_m1", "Apple M1"),
            ("apple_m2", "Apple M2"),
            ("apple_m3", "Apple M3"),
            ("apple_m4", "Apple M4"),
            ("snapdragon_x", "Snapdragon X"),
            ("mediatek", "MediaTek"),
        ),
    },
    # Any GPU can be typed. The suggestions are families across every vendor --
    # a starting point to complete ("NVIDIA GeForce RTX" -> "... RTX 4060 8GB"),
    # not a list of allowed models.
    "gpu": {
        "label": "Graphics",
        "type": "text",
        "options": _opts(
            ("integrated", "Integrated"),
            ("intel_uhd", "Intel UHD Graphics"),
            ("intel_iris_xe", "Intel Iris Xe"),
            ("intel_arc", "Intel Arc"),
            ("amd_radeon", "AMD Radeon Graphics"),
            ("amd_radeon_rx", "AMD Radeon RX"),
            ("amd_radeon_pro", "AMD Radeon Pro"),
            ("nvidia_mx", "NVIDIA GeForce MX"),
            ("nvidia_gtx", "NVIDIA GeForce GTX"),
            ("nvidia_rtx", "NVIDIA GeForce RTX"),
            ("nvidia_rtx_pro", "NVIDIA RTX (workstation)"),
            ("nvidia_quadro", "NVIDIA Quadro"),
            ("apple_gpu", "Apple GPU (integrated)"),
            ("qualcomm_adreno", "Qualcomm Adreno"),
        ),
    },
    # Pick or type: versions matter to buyers (Windows 10 vs 11, HarmonyOS).
    "os": {
        "label": "Operating system",
        "type": "text",
        "options": _opts(
            ("windows", "Windows"),
            ("windows_11", "Windows 11"),
            ("windows_10", "Windows 10"),
            ("macos", "macOS"),
            ("chromeos", "ChromeOS"),
            ("linux", "Linux"),
            ("harmonyos", "HarmonyOS"),
            ("android", "Android"),
            ("none", "None"),
        ),
    },
    "battery_cycles": {"label": "Battery cycle count", "type": "number", "min": 0},
    # --- audio ---
    "audio_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("earbuds", "Earbuds"),
            ("on_ear", "On-ear"),
            ("over_ear", "Over-ear"),
            ("speaker", "Speaker"),
            ("soundbar", "Soundbar"),
        ),
    },
    "noise_cancellation": {
        "label": "Noise cancelling",
        "type": "enum",
        "options": _opts(("yes", "Yes"), ("no", "No")),
    },
    "power_output": {"label": "Power output", "type": "number", "unit": "W", "min": 1},
    # Typed: ratings come in many forms (IPX4, IP54, IP68, 5ATM).
    "water_resistance": {
        "label": "Water resistance",
        "type": "text",
        "options": _opts(
            ("none", "None"),
            ("ipx4", "IPX4"),
            ("ipx5", "IPX5"),
            ("ipx6", "IPX6"),
            ("ipx7", "IPX7"),
            ("ip54", "IP54"),
            ("ip67", "IP67"),
            ("ip68", "IP68"),
        ),
    },
    # --- cameras ---
    "camera_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("mirrorless", "Mirrorless"),
            ("dslr", "DSLR"),
            ("compact", "Compact"),
            ("action", "Action"),
            ("drone", "Drone"),
        ),
    },
    "kit": {
        "label": "Kit",
        "type": "enum",
        "options": _opts(("body_only", "Body only"), ("with_lens", "With lens")),
    },
    # Pick or type: Leica L, Pentax K and older mounts are all real.
    "lens_mount": {
        "label": "Lens mount",
        "type": "text",
        "options": _opts(
            ("canon_rf", "Canon RF"),
            ("canon_ef", "Canon EF"),
            ("sony_e", "Sony E"),
            ("nikon_z", "Nikon Z"),
            ("nikon_f", "Nikon F"),
            ("fujifilm_x", "Fujifilm X"),
            ("micro_four_thirds", "Micro Four Thirds"),
            ("leica_l", "Leica L"),
        ),
    },
    "megapixels": {
        "label": "Resolution",
        "type": "number",
        "unit": "MP",
        "min": 1,
        "max": 400,
    },
    "shutter_count": {"label": "Shutter count", "type": "number", "min": 0},
    # --- accessories ---
    "accessory_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("charger", "Charger"),
            ("cable", "Cable"),
            ("power_bank", "Power bank"),
            ("case", "Case"),
            ("screen_protector", "Screen protector"),
            ("mouse_keyboard", "Mouse / keyboard"),
            ("external_storage", "External storage"),
            ("memory_card", "Memory card"),
            ("wearable", "Smartwatch / band"),
            ("other", "Other"),
        ),
    },
    # Pick or type: a case is for a specific model ("Pixel 8", "Redmi Note 13").
    "compatible_with": {
        "label": "Works with",
        "type": "text",
        "options": _opts(
            ("iphone", "iPhone"),
            ("samsung_galaxy", "Samsung Galaxy"),
            ("android_usb_c", "Android (USB-C)"),
            ("laptop_usb_c", "Laptops (USB-C)"),
            ("universal", "Universal"),
        ),
    },
    # Pick or type: "DC barrel", "USB-C to Lightning"...
    "connector": {
        "label": "Connector",
        "type": "text",
        "options": _opts(
            ("usb_c", "USB-C"),
            ("lightning", "Lightning"),
            ("micro_usb", "Micro-USB"),
            ("usb_a", "USB-A"),
            ("usb_c_to_lightning", "USB-C to Lightning"),
            ("dc_barrel", "DC barrel"),
        ),
    },
    "output_power": {"label": "Output power", "type": "number", "unit": "W", "min": 1},
    "battery_capacity": {
        "label": "Capacity",
        "type": "number",
        "unit": "mAh",
        "min": 100,
    },
    "cable_length": {
        "label": "Length",
        "type": "number",
        "unit": "m",
        "min": 0.1,
        "max": 20,
    },
}

# Compact on purpose: one field per line reads as a table. Black would explode
# every dict onto several lines.
# fmt: off
USED = {"condition": ["used"]}
WARRANTY = {"attribute": "warranty", "required": True,
            "placeholder": "e.g. Brand · 1 year"}
# `control: size` = a number with a GB / TB switch, stored in GB.
STORAGE = {"attribute": "storage", "required": True, "control": "size",
           "placeholder": "e.g. 128"}
RAM = {"attribute": "ram", "required": True, "placeholder": "e.g. 8"}


def _color(required=True):
    return {"attribute": "color", "required": required,
            "placeholder": "e.g. Black or Titanium Blue"}


PRODUCT_TYPES = {
    "smartphone": {
        "label": "Smartphone",
        "categories": ["phones"],
        "brands": [
            "Apple", "Samsung", "Tecno", "Infinix", "Xiaomi", "Itel", "Oppo",
            "Vivo", "Realme", "Huawei", "Honor", "Google", "Nokia", "Motorola", "OnePlus",
        ],
        "fields": [
            STORAGE,
            RAM,
            _color(),
            {"attribute": "sim", "required": True},
            {"attribute": "network"},
            {"attribute": "screen_size", "placeholder": "e.g. 6.1"},
            WARRANTY,
            {"attribute": "battery_health", "show_if": USED, "required_if": USED,
             "placeholder": "e.g. 92", "hint": "Settings › Battery on most phones."},
        ],
    },
    "laptop": {
        "label": "Laptop",
        "categories": ["laptops"],
        "brands": [
            "Apple", "Dell", "HP", "Lenovo", "Asus", "Acer",
            "MSI", "Microsoft", "Samsung", "Toshiba", "Huawei",
        ],
        "fields": [
            {"attribute": "laptop_type", "required": True, "placeholder": "e.g. Gaming"},
            {"attribute": "processor", "required": True,
             "placeholder": "e.g. Intel Core i5-1135G7",
             "hint": "Type the exact model; the sticker or System Info shows it."},
            {**RAM, "placeholder": "e.g. 16"},
            {"attribute": "storage_type", "required": True},
            {**STORAGE, "placeholder": "e.g. 512",
             "hint": "For SSD + HDD, the SSD size."},
            {"attribute": "secondary_storage", "required": True, "control": "size",
             "placeholder": "e.g. 1", "show_if": {"storage_type": ["ssd_hdd"]}},
            {"attribute": "screen_size", "required": True, "placeholder": "e.g. 15.6"},
            _color(),
            {"attribute": "touchscreen"},
            {"attribute": "os", "placeholder": "e.g. Windows 11"},
            {"attribute": "gpu", "placeholder": "e.g. NVIDIA GeForce RTX 4060 8GB",
             "hint": "Any brand or model; the suggestions are only a start."},
            WARRANTY,
            {"attribute": "battery_cycles", "show_if": USED, "required_if": USED,
             "placeholder": "e.g. 180"},
        ],
    },
    "tablet": {
        "label": "Tablet",
        "categories": ["tablets"],
        "brands": [
            "Apple", "Samsung", "Lenovo", "Xiaomi", "Huawei", "Microsoft", "Amazon", "Tecno",
        ],
        "fields": [
            STORAGE,
            {"attribute": "connectivity", "required": True,
             "options": ["wifi", "wifi_cellular"]},
            _color(),
            {**RAM, "required": False},
            {"attribute": "screen_size", "placeholder": "e.g. 10.9"},
            WARRANTY,
            {"attribute": "battery_health", "show_if": USED, "required_if": USED,
             "placeholder": "e.g. 90"},
        ],
    },
    "audio": {
        "label": "Audio",
        "categories": ["audio"],
        "brands": [
            "Apple", "Sony", "JBL", "Bose", "Samsung", "Anker", "Beats",
            "Sennheiser", "Marshall", "Oraimo", "Xiaomi", "Huawei",
        ],
        "fields": [
            {"attribute": "audio_type", "required": True},
            {"attribute": "connectivity", "label": "Connection", "required": True,
             "options": ["bluetooth", "wired", "bluetooth_wired"]},
            {"attribute": "noise_cancellation",
             "show_if": {"audio_type": ["earbuds", "on_ear", "over_ear"]}},
            {"attribute": "power_output", "placeholder": "e.g. 30",
             "show_if": {"audio_type": ["speaker", "soundbar"]}},
            {"attribute": "water_resistance", "placeholder": "e.g. IPX4"},
            _color(),
            WARRANTY,
        ],
    },
    "camera": {
        "label": "Camera",
        "categories": ["cameras"],
        "brands": [
            "Canon", "Sony", "Nikon", "Fujifilm", "Panasonic", "GoPro", "DJI", "Insta360",
        ],
        "fields": [
            {"attribute": "camera_type", "required": True},
            {"attribute": "kit", "required": True,
             "show_if": {"camera_type": ["mirrorless", "dslr"]}},
            {"attribute": "lens_mount", "placeholder": "e.g. Sony E",
             "show_if": {"camera_type": ["mirrorless", "dslr"]}},
            {"attribute": "megapixels", "placeholder": "e.g. 24"},
            WARRANTY,
            {"attribute": "shutter_count", "placeholder": "e.g. 12000",
             "show_if": {"condition": ["used"], "camera_type": ["mirrorless", "dslr"]},
             "required_if": USED},
        ],
    },
    "accessory": {
        "label": "Accessory",
        "categories": ["accessories"],
        "brands": [
            "Apple", "Samsung", "Anker", "Oraimo", "Baseus", "Ugreen",
            "Logitech", "Belkin", "Xiaomi", "SanDisk", "Kingston",
        ],
        "fields": [
            {"attribute": "accessory_type", "required": True, "control": "select"},
            {"attribute": "compatible_with", "placeholder": "e.g. iPhone 15 Pro",
             "required_if": {"accessory_type": ["case", "screen_protector"]}},
            {"attribute": "connector", "placeholder": "e.g. USB-C",
             "show_if": {"accessory_type": ["charger", "cable", "power_bank"]}},
            {"attribute": "output_power", "placeholder": "e.g. 20",
             "show_if": {"accessory_type": ["charger", "power_bank"]},
             "required_if": {"accessory_type": ["charger", "power_bank"]}},
            {"attribute": "battery_capacity", "placeholder": "e.g. 10000",
             "show_if": {"accessory_type": ["power_bank"]},
             "required_if": {"accessory_type": ["power_bank"]}},
            {"attribute": "cable_length", "placeholder": "e.g. 1",
             "show_if": {"accessory_type": ["cable"]}},
            {**STORAGE, "label": "Capacity", "placeholder": "e.g. 256",
             "show_if": {"accessory_type": ["external_storage", "memory_card"]},
             "required": False,
             "required_if": {"accessory_type": ["external_storage", "memory_card"]}},
            _color(required=False),
            WARRANTY,
        ],
    },
}
# fmt: on
