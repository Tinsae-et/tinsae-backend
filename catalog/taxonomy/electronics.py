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


_YES_NO = _opts(("yes", "Yes"), ("no", "No"))


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
            ("triple", "Triple"),
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
    # --- spec sheet: display, performance, camera, battery, body ---
    "resolution": {
        "label": "Resolution",
        "type": "text",
        "options": _opts(
            ("hd", "HD (1366×768)"),
            ("hd_plus", "HD+ (1600×900)"),
            ("fhd", "FHD (1920×1080)"),
            ("fhd_plus", "FHD+ (1920×1200)"),
            ("qhd", "QHD (2560×1440)"),
            ("qhd_plus", "QHD+ (2560×1600)"),
            ("3k", "3K (2880×1800)"),
            ("4k", "4K UHD (3840×2160)"),
        ),
    },
    "display_panel": {
        "label": "Display type",
        "type": "text",
        "options": _opts(
            ("ips_lcd", "IPS LCD"),
            ("lcd", "LCD"),
            ("tn", "TN"),
            ("oled", "OLED"),
            ("amoled", "AMOLED"),
            ("super_amoled", "Super AMOLED"),
            ("dynamic_amoled_2x", "Dynamic AMOLED 2X"),
            ("ltpo_oled", "LTPO OLED"),
            ("mini_led", "Mini-LED"),
            ("liquid_retina", "Liquid Retina"),
        ),
    },
    "refresh_rate": {
        "label": "Refresh rate",
        "type": "number",
        "unit": "Hz",
        "min": 30,
        "max": 540,
    },
    "chipset": {
        "label": "Chipset",
        "type": "text",
        "options": _opts(
            ("apple_a15", "Apple A15 Bionic"),
            ("apple_a16", "Apple A16 Bionic"),
            ("apple_a17_pro", "Apple A17 Pro"),
            ("apple_a18", "Apple A18"),
            ("snapdragon_8_gen_3", "Snapdragon 8 Gen 3"),
            ("snapdragon_8_gen_2", "Snapdragon 8 Gen 2"),
            ("snapdragon_7", "Snapdragon 7 series"),
            ("snapdragon_6", "Snapdragon 6 series"),
            ("exynos_2400", "Exynos 2400"),
            ("exynos_1380", "Exynos 1380"),
            ("dimensity_9300", "MediaTek Dimensity 9300"),
            ("dimensity_7200", "MediaTek Dimensity 7200"),
            ("helio_g99", "MediaTek Helio G99"),
            ("helio_g85", "MediaTek Helio G85"),
            ("tensor_g3", "Google Tensor G3"),
            ("unisoc_t606", "Unisoc T606"),
        ),
    },
    "main_camera": {
        "label": "Main camera",
        "type": "number",
        "unit": "MP",
        "min": 0.3,
        "max": 400,
    },
    "camera_setup": {
        "label": "Camera setup",
        "type": "enum",
        "options": _opts(
            ("single", "Single"),
            ("dual", "Dual"),
            ("triple", "Triple"),
            ("quad", "Quad"),
        ),
    },
    "selfie_camera": {
        "label": "Selfie camera",
        "type": "number",
        "unit": "MP",
        "min": 0.3,
        "max": 200,
    },
    "fast_charging": {
        "label": "Charging speed",
        "type": "number",
        "unit": "W",
        "min": 1,
        "max": 300,
    },
    "wireless_charging": {
        "label": "Wireless charging",
        "type": "enum",
        "options": _YES_NO,
    },
    "nfc": {"label": "NFC", "type": "enum", "options": _YES_NO},
    "card_slot": {"label": "Memory card slot", "type": "enum", "options": _YES_NO},
    "headphone_jack": {"label": "3.5 mm jack", "type": "enum", "options": _YES_NO},
    "weight": {
        "label": "Weight",
        "type": "number",
        "unit": "g",
        "min": 1,
        "max": 20000,
    },
    "release_year": {
        "label": "Release year",
        "type": "number",
        "min": 1990,
        "max": 2100,
    },
    "phone_type": {
        "label": "Phone type",
        "type": "enum",
        "options": _opts(
            ("smartphone", "Smartphone"),
            ("feature_phone", "Feature phone"),
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
    "ram_type": {
        "label": "RAM type",
        "type": "text",
        "options": _opts(
            ("ddr3", "DDR3"),
            ("ddr4", "DDR4"),
            ("ddr5", "DDR5"),
            ("lpddr4x", "LPDDR4X"),
            ("lpddr5", "LPDDR5"),
            ("lpddr5x", "LPDDR5X"),
            ("unified", "Unified memory"),
        ),
    },
    "ram_upgradeable": {
        "label": "RAM upgrade",
        "type": "enum",
        "options": _opts(("upgradeable", "Upgradeable"), ("soldered", "Soldered")),
    },
    "storage_interface": {
        "label": "SSD interface",
        "type": "enum",
        "options": _opts(("nvme", "NVMe"), ("sata", "SATA")),
    },
    "gpu_type": {
        "label": "Graphics type",
        "type": "enum",
        "options": _opts(("integrated", "Integrated"), ("dedicated", "Dedicated")),
    },
    "vram": {
        "label": "Graphics memory",
        "type": "number",
        "unit": "GB",
        "min": 0.5,
        "max": 128,
    },
    "keyboard_layout": {
        "label": "Keyboard",
        "type": "text",
        "options": _opts(
            ("us", "US English"),
            ("uk", "UK English"),
            ("arabic", "Arabic / English"),
            ("french", "French"),
            ("german", "German"),
        ),
    },
    "backlit_keyboard": {
        "label": "Backlit keyboard",
        "type": "enum",
        "options": _YES_NO,
    },
    "fingerprint": {"label": "Fingerprint reader", "type": "enum", "options": _YES_NO},
    "wifi_version": {
        "label": "Wi-Fi",
        "type": "text",
        "options": _opts(
            ("wifi_5", "Wi-Fi 5"),
            ("wifi_6", "Wi-Fi 6"),
            ("wifi_6e", "Wi-Fi 6E"),
            ("wifi_7", "Wi-Fi 7"),
        ),
    },
    "bluetooth_version": {
        "label": "Bluetooth",
        "type": "text",
        "options": _opts(
            ("bt_4_2", "4.2"),
            ("bt_5_0", "5.0"),
            ("bt_5_1", "5.1"),
            ("bt_5_2", "5.2"),
            ("bt_5_3", "5.3"),
            ("bt_5_4", "5.4"),
        ),
    },
    "battery_life": {
        "label": "Battery life",
        "type": "number",
        "unit": "h",
        "min": 0.5,
        "max": 100,
    },
}

# Compact on purpose: one field per line reads as a table. Black would explode
# every dict onto several lines.
# fmt: off
USED = {"condition": ["used"]}
# Sections: key (default) = what buyers filter on; details = the spec sheet,
# collapsible in the form; item = facts about this unit. See the model's
# ProductTypeAttribute.section.
DETAIL = {"section": "details"}
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
        "categories": ["phones"],
        "brands": [
            "Apple", "Samsung", "Tecno", "Infinix", "Xiaomi", "Itel", "Oppo",
            "Vivo", "Realme", "Huawei", "Honor", "Google", "Nokia", "Motorola", "OnePlus",
        ],
        "fields": [
            # Key specs: what buyers filter on.
            {"attribute": "phone_type", "required": True},
            STORAGE,
            RAM,
            _color(),
            {"attribute": "sim", "required": True},
            {"attribute": "network", "required": True},
            # Spec sheet: recommended leads the product page's key-spec card.
            {**DETAIL, "attribute": "screen_size", "recommended": True,
             "placeholder": "e.g. 6.1"},
            {**DETAIL, "attribute": "display_panel", "recommended": True,
             "placeholder": "e.g. AMOLED"},
            {**DETAIL, "attribute": "refresh_rate", "recommended": True,
             "placeholder": "e.g. 120"},
            {**DETAIL, "attribute": "resolution"},
            {**DETAIL, "attribute": "chipset", "recommended": True,
             "placeholder": "e.g. Snapdragon 8 Gen 3"},
            {**DETAIL, "attribute": "main_camera", "recommended": True,
             "placeholder": "e.g. 50"},
            {**DETAIL, "attribute": "camera_setup", "recommended": True},
            {**DETAIL, "attribute": "selfie_camera", "placeholder": "e.g. 12"},
            {**DETAIL, "attribute": "battery_capacity", "label": "Battery",
             "recommended": True, "placeholder": "e.g. 5000"},
            {**DETAIL, "attribute": "fast_charging", "recommended": True,
             "placeholder": "e.g. 25"},
            {**DETAIL, "attribute": "wireless_charging"},
            {**DETAIL, "attribute": "os", "recommended": True,
             "placeholder": "e.g. Android 14"},
            {**DETAIL, "attribute": "release_year", "recommended": True,
             "placeholder": "e.g. 2024"},
            {**DETAIL, "attribute": "weight", "control": "weight", "recommended": True,
             "placeholder": "e.g. 187"},
            {**DETAIL, "attribute": "nfc"},
            {**DETAIL, "attribute": "card_slot"},
            {**DETAIL, "attribute": "headphone_jack"},
            {**DETAIL, "attribute": "water_resistance", "label": "IP rating",
             "placeholder": "e.g. IP68"},
            # This item: facts about the unit in the shop.
            {**ITEM, "attribute": "carrier_lock", "required": True},
            {**ITEM, "attribute": "region_version", "required": True,
             "placeholder": "e.g. Global",
             "hint": "Imported phones: the market it was made for."},
            WARRANTY,
            {**ITEM, "attribute": "battery_health", "show_if": USED,
             "required_if": USED, "placeholder": "e.g. 92",
             "hint": "Settings › Battery on most phones."},
            {**ITEM, "attribute": "box_contents", "placeholder": "e.g. Full box"},
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
            # Key specs.
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
            # Spec sheet.
            {**DETAIL, "attribute": "ram_type", "recommended": True,
             "placeholder": "e.g. DDR4"},
            {**DETAIL, "attribute": "ram_upgradeable"},
            {**DETAIL, "attribute": "storage_interface",
             "show_if": {"storage_type": ["ssd", "ssd_hdd"]}},
            {**DETAIL, "attribute": "resolution", "recommended": True,
             "placeholder": "e.g. FHD (1920×1080)"},
            {**DETAIL, "attribute": "display_panel", "recommended": True,
             "placeholder": "e.g. IPS LCD"},
            {**DETAIL, "attribute": "refresh_rate", "placeholder": "e.g. 144"},
            {**DETAIL, "attribute": "gpu", "recommended": True,
             "placeholder": "e.g. NVIDIA GeForce RTX 4060",
             "hint": "Any brand or model; the suggestions are only a start."},
            {**DETAIL, "attribute": "gpu_type", "recommended": True},
            {**DETAIL, "attribute": "vram", "placeholder": "e.g. 8",
             "show_if": {"gpu_type": ["dedicated"]}},
            {**DETAIL, "attribute": "os", "recommended": True,
             "placeholder": "e.g. Windows 11"},
            {**DETAIL, "attribute": "keyboard_layout", "recommended": True,
             "placeholder": "e.g. US English"},
            {**DETAIL, "attribute": "backlit_keyboard"},
            {**DETAIL, "attribute": "battery_life", "recommended": True,
             "placeholder": "e.g. 8"},
            {**DETAIL, "attribute": "weight", "control": "weight", "recommended": True,
             "placeholder": "e.g. 1.4", "hint": "Switch to kg for laptops."},
            {**DETAIL, "attribute": "touchscreen"},
            {**DETAIL, "attribute": "fingerprint"},
            {**DETAIL, "attribute": "wifi_version", "placeholder": "e.g. Wi-Fi 6"},
            {**DETAIL, "attribute": "bluetooth_version", "placeholder": "e.g. 5.2"},
            {**DETAIL, "attribute": "release_year", "placeholder": "e.g. 2023"},
            # This item.
            WARRANTY,
            {**ITEM, "attribute": "battery_cycles", "show_if": USED,
             "required_if": USED, "placeholder": "e.g. 180"},
            {**ITEM, "attribute": "charger_included"},
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
