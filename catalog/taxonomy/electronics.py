"""Electronics: phones, tablets, wearables, computers, TV & audio, cameras and
accessories, and the specs their listings carry.

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


_YES_NO = _opts(("yes", "Yes"), ("no", "No"))


# Storage and RAM were enums of sizes ("256gb"). `convert_from` tells the sync
# to convert stored values to numbers in GB once, in the same transaction; see
# catalog/taxonomy/sync.py `CONVERTERS`.
SIZE_FROM_ENUM = {"type": "enum", "using": "size_gb"}
# A closed list opened to pick-or-type: stored codes become their labels.
OPEN_LIST = {"type": "enum", "using": "option_label"}

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
            # Fashion colours.
            ("navy", "Navy"),
            ("beige", "Beige"),
            ("multicolor", "Multicolour"),
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
        "convert_from": SIZE_FROM_ENUM,
    },
    "ram": {
        "label": "RAM",
        "type": "number",
        "unit": "GB",
        # 0.5 for the 512MB feature-class phones still sold.
        "min": 0.5,
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
    },
    # --- phones & tablets ---
    "sim": {
        "label": "SIM",
        "type": "enum",
        "options": _opts(
            ("single", "Single"),
            ("dual", "Dual"),
            ("triple", "Triple"),
            ("esim", "eSIM only"),
            ("sim_esim", "SIM + eSIM"),
        ),
    },
    "network": {
        "label": "Network",
        "type": "enum",
        "options": _opts(("2g", "2G"), ("3g", "3G"), ("4g", "4G"), ("5g", "5G")),
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
            ("home_theatre", "Home theatre"),
            ("other", "Other"),
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
            ("5atm", "5ATM"),
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
            ("other", "Other"),
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
    },
    # --- spec sheet: display, performance, camera, battery, body ---
    "refresh_rate": {
        "label": "Refresh rate",
        "type": "number",
        "unit": "Hz",
        "min": 30,
    },
    "main_camera": {
        "label": "Main camera",
        "type": "number",
        "unit": "MP",
        "min": 0.3,
    },
    "phone_type": {
        "label": "Phone type",
        "type": "enum",
        "options": _opts(
            ("smartphone", "Smartphone"),
            ("feature_phone", "Basic / keypad phone"),
            ("rugged", "Rugged"),
        ),
    },
    # --- this unit: imported phones are asked about these first ---
    "carrier_lock": {
        "label": "Network lock",
        "type": "enum",
        "options": _opts(("unlocked", "Unlocked"), ("locked", "Carrier-locked")),
    },
    "region_version": {
        "label": "Version",
        "type": "text",
        "options": _opts(
            ("global", "Global"),
            ("us", "US"),
            ("uae", "UAE"),
            ("europe", "Europe"),
            ("china", "China"),
            ("india", "India"),
            ("japan", "Japan"),
            ("korea", "Korea"),
            ("hong_kong", "Hong Kong"),
        ),
    },
    "box_contents": {
        "label": "In the box",
        "type": "text",
        "options": _opts(
            ("full_box", "Full box (all accessories)"),
            ("box_charger", "Box + charger"),
            ("box_cable", "Box + cable"),
            ("device_only", "Device only"),
        ),
    },
    "charger_included": {
        "label": "Charger included",
        "type": "enum",
        "options": _YES_NO,
    },
    # --- laptops ---
    "battery_life": {
        "label": "Battery life",
        "type": "number",
        "unit": "h",
        "min": 0.5,
    },
    # --- desktops ---
    "form_factor": {
        "label": "Form factor",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("tower", "Tower"),
            ("all_in_one", "All-in-one"),
            ("mini_pc", "Mini PC"),
        ),
    },
    "monitor_included": {
        "label": "Monitor included",
        "type": "enum",
        "options": _YES_NO,
    },
    # --- monitors & TVs ---
    # A closed list, unlike laptops' pick-or-type `resolution`: screens are sold
    # by these names, and buyers filter on them.
    "display_resolution": {
        "label": "Resolution",
        "type": "enum",
        "options": _opts(
            ("hd", "HD (720p)"),
            ("fhd", "Full HD (1080p)"),
            ("qhd", "QHD (1440p)"),
            ("4k", "4K UHD"),
            ("8k", "8K"),
        ),
    },
    "monitor_panel": {
        "label": "Panel",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(("ips", "IPS"), ("va", "VA"), ("oled", "OLED"), ("tn", "TN")),
    },
    "curved": {"label": "Curved", "type": "enum", "options": _YES_NO},
    "smart_tv": {"label": "Smart TV", "type": "enum", "options": _YES_NO},
    # Pick or type: brands ship their own platforms (VIDAA, Roku...).
    "tv_os": {
        "label": "Smart platform",
        "type": "text",
        "options": _opts(
            ("android_tv", "Android TV"),
            ("google_tv", "Google TV"),
            ("tizen", "Tizen"),
            ("webos", "webOS"),
            ("vidaa", "VIDAA"),
        ),
    },
    # --- wearables ---
    "wearable_type": {
        "label": "Type",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("smartwatch", "Smartwatch"),
            ("fitness_band", "Fitness band"),
            ("smart_ring", "Smart ring"),
            ("kids_watch", "Kids watch"),
        ),
    },
    # A checklist (multiple values), not a level: buyers filter on the one
    # sensor they need, an ECG or blood oxygen. A new key, because the old
    # `health_sensors` held a level and a key never changes meaning.
    "sensors": {
        "label": "Health sensors",
        "type": "enum",
        "options": _opts(
            ("heart_rate", "Heart rate"),
            ("spo2", "Blood oxygen (SpO2)"),
            ("sleep", "Sleep tracking"),
            ("stress", "Stress"),
            ("ecg", "ECG"),
            ("blood_pressure", "Blood pressure"),
            ("temperature", "Skin temperature"),
        ),
    },
    "calling": {
        "label": "Calling",
        "type": "enum",
        "options": _opts(
            ("none", "None"),
            ("bluetooth", "Bluetooth calling"),
            ("lte", "LTE / eSIM"),
        ),
    },
    "gps": {
        "label": "GPS",
        "type": "enum",
        "options": _opts(
            ("built_in", "Built-in"),
            ("phone", "Uses phone GPS"),
            ("none", "None"),
        ),
    },
    "battery_days": {
        "label": "Battery life",
        "type": "number",
        "unit": "days",
        "min": 0.5,
    },
    # --- cameras ---
    "video_resolution": {
        "label": "Video",
        "type": "enum",
        "options": _opts(("fhd", "Full HD"), ("4k", "4K"), ("6k", "6K and above")),
    },
    "works_with": {
        "label": "Works with",
        "type": "enum",
        "options": _opts(
            ("iphone", "iPhone"),
            ("android", "Android"),
            ("both", "iPhone & Android"),
        ),
    },
}

# Compact on purpose: one field per line reads as a table. Black would explode
# every dict onto several lines.
# fmt: off
USED = {"condition": ["used"]}
# Sections: key (default) = what buyers filter on; item = facts about this unit
# (warranty, battery health...). See the model's ProductTypeAttribute.section.
ITEM = {"section": "item"}
WARRANTY = {"attribute": "warranty", "required": True, "section": "item",
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
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "iPhone 15 Pro", "variant": "256GB \u00b7 Natural Titanium"},
        "categories": ["phones"],
        "brands": [
            "Apple", "Samsung", "Tecno", "Infinix", "Xiaomi", "Itel", "Oppo",
            "Vivo", "Realme", "Huawei", "Honor", "Google", "Nokia", "Motorola", "OnePlus",
        ],
        "fields": [
            # A keypad phone has no storage, RAM or camera to list, so those
            # fields are smart/rugged only, keyed off the type.
            {"attribute": "phone_type", "required": True},
            {**STORAGE, "show_if": {"phone_type": ["smartphone", "rugged"]}},
            {**RAM, "show_if": {"phone_type": ["smartphone", "rugged"]}},
            _color(),
            {"attribute": "sim", "required": True},
            {"attribute": "network", "required": True},
            {"attribute": "screen_size", "recommended": True,
             "placeholder": "e.g. 6.1"},
            # Spec sheet: recommended leads the product page's key-spec card.
            {"attribute": "main_camera", "recommended": True,
             "placeholder": "e.g. 50",
             "show_if": {"phone_type": ["smartphone", "rugged"]}},
            {"attribute": "battery_capacity", "label": "Battery",
             "recommended": True, "placeholder": "e.g. 5000"},
            # This item: facts about the unit in the shop.
            {**ITEM, "attribute": "carrier_lock", "required": True},
            {**ITEM, "attribute": "region_version", "placeholder": "e.g. Global",
             "hint": "Imported phones: the market it was made for."},
            WARRANTY,
            {**ITEM, "attribute": "battery_health",
             "show_if": {"condition": ["used"],
                         "phone_type": ["smartphone", "rugged"]},
             "required_if": {"condition": ["used"],
                             "phone_type": ["smartphone", "rugged"]},
             "placeholder": "e.g. 92",
             "hint": "Settings › Battery on most phones."},
            {**ITEM, "attribute": "box_contents", "placeholder": "e.g. Full box"},
        ],
    },
    "laptop": {
        "label": "Laptop",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "MacBook Air 13 M3", "variant": "16GB \u00b7 512GB SSD"},
        "categories": ["laptops"],
        "brands": [
            "Apple", "Dell", "HP", "Lenovo", "Asus", "Acer",
            "MSI", "Microsoft", "Samsung", "Toshiba", "Huawei",
        ],
        "fields": [
            # Key specs.
            {"attribute": "laptop_type", "placeholder": "e.g. Gaming"},
            {"attribute": "os", "recommended": True, "placeholder": "e.g. Windows 11"},
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
            _color(required=False),
            # A key spec, not buried in the full spec sheet: for a gaming or
            # workstation laptop the GPU is what the buyer is choosing on.
            {"attribute": "gpu", "recommended": True,
             "placeholder": "e.g. NVIDIA GeForce RTX 4060 8GB",
             "hint": "Any brand or model; the suggestions are only a start."},
            # This item.
            WARRANTY,
            {**ITEM, "attribute": "battery_cycles", "show_if": USED,
             "required_if": USED, "placeholder": "e.g. 180"},
            {**ITEM, "attribute": "charger_included"},
        ],
    },
    "tablet": {
        "label": "Tablet",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "iPad Air 11 (M2)", "variant": "128GB \u00b7 Wi-Fi"},
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
            {"attribute": "screen_size", "required": True,
             "placeholder": "e.g. 10.9"},
            WARRANTY,
            {**ITEM, "attribute": "battery_health", "show_if": USED,
             "required_if": USED, "placeholder": "e.g. 90"},
        ],
    },
    "audio": {
        "label": "Audio",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "WH-1000XM5", "variant": "Black \u00b7 Wireless"},
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
            # The first question for wireless headphones and speakers.
            {"attribute": "battery_life", "placeholder": "e.g. 30",
             "show_if": {"connectivity": ["bluetooth", "bluetooth_wired"]}},
            {"attribute": "water_resistance", "placeholder": "e.g. IPX4",
             "show_if": {"audio_type": ["earbuds", "on_ear", "over_ear", "speaker"]}},
            _color(),
            WARRANTY,
        ],
    },
    "camera": {
        "label": "Camera",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Alpha 7 IV", "variant": "Body only"},
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
            {"attribute": "video_resolution"},
            WARRANTY,
            {"attribute": "shutter_count", "placeholder": "e.g. 12000",
             "show_if": {"condition": ["used"], "camera_type": ["mirrorless", "dslr"]},
             "required_if": {"condition": ["used"],
                             "camera_type": ["mirrorless", "dslr"]}},
        ],
    },
    "wearable": {
        "label": "Wearable",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Apple Watch Series 9", "variant": "45mm \u00b7 GPS"},
        "categories": ["wearables"],
        "brands": [
            "Apple", "Samsung", "Xiaomi", "Huawei", "Amazfit", "Oraimo", "Garmin",
        ],
        "fields": [
            {"attribute": "wearable_type", "required": True},
            {"attribute": "works_with"},
            {"attribute": "sensors", "recommended": True, "multiple": True},
            {"attribute": "calling", "recommended": True},
            {"attribute": "gps"},
            {"attribute": "battery_days", "recommended": True, "placeholder": "e.g. 7"},
            {"attribute": "water_resistance", "placeholder": "e.g. 5ATM"},
            _color(required=False),
            WARRANTY,
        ],
    },
    "desktop": {
        "label": "Desktop",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "OptiPlex 7010 Tower", "variant": "Core i5 \u00b7 16GB \u00b7 512GB SSD"},
        "categories": ["desktops"],
        "brands": ["Dell", "HP", "Lenovo", "Apple", "Asus", "Acer", "MSI"],
        "fields": [
            {"attribute": "form_factor", "required": True},
            {"attribute": "os", "recommended": True, "placeholder": "e.g. Windows 11"},
            {"attribute": "processor", "required": True,
             "placeholder": "e.g. Intel Core i5-12400"},
            {**RAM, "placeholder": "e.g. 16"},
            {"attribute": "storage_type", "required": True},
            {**STORAGE, "placeholder": "e.g. 512"},
            {"attribute": "secondary_storage", "required": True, "control": "size",
             "placeholder": "e.g. 1", "show_if": {"storage_type": ["ssd_hdd"]}},
            {"attribute": "gpu", "recommended": True,
             "placeholder": "e.g. NVIDIA GeForce RTX 4060 8GB"},
            {**ITEM, "attribute": "monitor_included"},
            WARRANTY,
        ],
    },
    "monitor": {
        "label": "Monitor",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Odyssey G5 27\"", "variant": "QHD \u00b7 165Hz"},
        "categories": ["monitors"],
        "brands": ["Samsung", "LG", "Dell", "HP", "Asus", "AOC", "Lenovo", "BenQ"],
        "fields": [
            {"attribute": "screen_size", "required": True, "placeholder": "e.g. 27"},
            {"attribute": "display_resolution", "required": True},
            {"attribute": "refresh_rate", "placeholder": "e.g. 144"},
            {"attribute": "monitor_panel"},
            {"attribute": "curved"},
            WARRANTY,
        ],
    },
    "tv": {
        "label": "TV",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "55\" Crystal UHD", "variant": "4K \u00b7 Smart TV"},
        "categories": ["tvs"],
        "brands": [
            "Samsung", "LG", "Sony", "Hisense", "TCL", "Skyworth", "Xiaomi", "Haier",
        ],
        "fields": [
            {"attribute": "screen_size", "required": True, "placeholder": "e.g. 55"},
            {"attribute": "display_resolution", "required": True},
            {"attribute": "smart_tv", "required": True},
            {"attribute": "refresh_rate", "placeholder": "e.g. 120"},
            {"attribute": "tv_os", "placeholder": "e.g. Google TV",
             "show_if": {"smart_tv": ["yes"]}},
            WARRANTY,
        ],
    },
    "accessory": {
        "label": "Accessory",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "20W USB-C Charger", "variant": "White"},
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
