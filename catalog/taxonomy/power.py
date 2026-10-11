"""Power & Solar: generators, solar panels and kits, inverters and batteries.

Same rules as electronics.py. Backup power is a major purchase in Addis because
of power cuts, and buyers compare brands, so `brand_required` stays on. Power
banks are phone accessories and stay in Accessories. `fuel`, `warranty` and
`operating_hours` are shared attributes defined elsewhere.
"""

from .electronics import ITEM, OPEN_LIST, USED, WARRANTY, _opts

_YES_NO = _opts(("yes", "Yes"), ("no", "No"))

ATTRIBUTES = {
    # --- generators ---
    # Generators are sold by apparent power; a 5 kVA unit runs a household.
    "power_kva": {
        "label": "Power",
        "type": "number",
        "unit": "kVA",
        "min": 0.5,
    },
    # Homes are single-phase; workshops and businesses often need three.
    "phase": {
        "label": "Phase",
        "type": "enum",
        "options": _opts(("single", "Single-phase"), ("three", "Three-phase")),
    },
    "start_type": {
        "label": "Start",
        "type": "enum",
        "options": _opts(("manual", "Manual (recoil)"), ("electric", "Electric")),
    },
    # "How long will it last?": hours on one full tank at a typical load.
    "runtime_h": {
        "label": "Runtime per tank",
        "type": "number",
        "unit": "h",
        "min": 0.5,
    },
    "silent": {"label": "Silent / canopy", "type": "enum", "options": _YES_NO},
    # --- solar ---
    "solar_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("panel", "Solar panel"),
            ("kit", "Complete kit"),
            ("light", "Solar light"),
            ("water_heater", "Solar water heater"),
            ("water_pump", "Solar water pump"),
            ("other", "Other"),
        ),
    },
    # Rated output of a panel or a kit's panels, or an inverter's load.
    "power_w": {
        "label": "Power",
        "type": "number",
        "unit": "W",
        "min": 1,
    },
    "panel_tech": {
        "label": "Panel type",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("mono", "Monocrystalline"),
            ("poly", "Polycrystalline"),
            ("bifacial", "Bifacial"),
        ),
    },
    # How long a kit or a solar light runs after dark.
    "battery_wh": {
        "label": "Battery capacity",
        "type": "number",
        "unit": "Wh",
        "min": 1,
    },
    "inverter_included": {
        "label": "Inverter included",
        "type": "enum",
        "options": _YES_NO,
    },
    # --- inverters & batteries ---
    "backup_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("inverter", "Inverter"),
            ("ups", "UPS"),
            ("battery", "Battery"),
            ("stabilizer", "Stabilizer"),
            ("other", "Other"),
        ),
    },
    # Pure sine runs fridges, pumps and laptops safely; modified sine is cheaper.
    "output_wave": {
        "label": "Output wave",
        "type": "enum",
        "options": _opts(
            ("pure_sine", "Pure sine"), ("modified_sine", "Modified sine")
        ),
    },
    # A hybrid inverter takes solar panels directly: the first question for
    # anyone pairing backup with solar.
    "solar_input": {
        "label": "Solar input (hybrid)",
        "type": "enum",
        "options": _YES_NO,
    },
    # Battery and inverter must match.
    "system_voltage": {
        "label": "System voltage",
        "type": "enum",
        "options": _opts(("12v", "12 V"), ("24v", "24 V"), ("48v", "48 V")),
    },
    "battery_chemistry": {
        "label": "Battery type",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("lithium", "Lithium (LiFePO4)"),
            ("lead_acid", "Lead-acid"),
            ("gel", "Gel"),
            ("tubular", "Tubular"),
            ("agm", "AGM"),
        ),
    },
    # Labelled and sold in Ah at the system voltage (Ah x V gives the energy).
    "capacity_ah": {
        "label": "Capacity",
        "type": "number",
        "unit": "Ah",
        "min": 1,
    },
}

# fmt: off
def _only(field, *values):
    """Shown, and filterable, only for these values of a type field."""
    return {"show_if": {field: list(values)}}


PRODUCT_TYPES = {
    "generator": {
        "label": "Generator",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "EU70is Inverter Generator", "variant": "7 kVA · Petrol"},
        "categories": ["generators"],
        "brands": [
            "Honda", "Yamaha", "Mitsubishi", "Perkins", "Cummins", "Firman",
            "Kipor", "Elemax",
        ],
        "fields": [
            {"attribute": "power_kva", "required": True, "placeholder": "e.g. 5"},
            {"attribute": "fuel", "required": True, "options": ["petrol", "diesel", "lpg"]},
            {"attribute": "phase", "required": True},
            {"attribute": "start_type"},
            {"attribute": "runtime_h", "recommended": True, "placeholder": "e.g. 8"},
            {"attribute": "silent"},
            # A used generator wears by engine hours, like machinery.
            {**ITEM, "attribute": "operating_hours", "placeholder": "e.g. 1200",
             "show_if": USED, "required_if": USED},
            WARRANTY,
        ],
    },
    "solar": {
        "label": "Solar",
        "listing_examples": {"name": "Solar Home Kit", "variant": "300 W · 1.2 kWh battery"},
        "categories": ["solar"],
        "brands": [
            "Jinko", "Longi", "Canadian Solar", "Trina", "Felicity", "Sun King",
            "d.light",
        ],
        "fields": [
            {"attribute": "solar_type", "required": True},
            {"attribute": "power_w", "required": True, "placeholder": "e.g. 450"},
            {"attribute": "panel_tech", **_only("solar_type", "panel", "kit")},
            {"attribute": "battery_wh", "recommended": True, "placeholder": "e.g. 1200",
             **_only("solar_type", "kit", "light")},
            {"attribute": "inverter_included", "recommended": True,
             **_only("solar_type", "kit")},
            WARRANTY,
        ],
    },
    "power_backup": {
        "label": "Inverters & batteries",
        "listing_examples": {"name": "IVEM 5048 Hybrid Inverter", "variant": "5 kW · 48 V"},
        "categories": ["inverters-batteries"],
        "brands": [
            "Felicity", "Growatt", "Victron", "Luminous", "Must", "Deye",
            "Ritar", "APC",
        ],
        "fields": [
            {"attribute": "backup_type", "required": True},
            {"attribute": "power_w", "placeholder": "e.g. 5000",
             **_only("backup_type", "inverter", "ups", "stabilizer"),
             "required_if": {"backup_type": ["inverter", "ups", "stabilizer"]}},
            {"attribute": "output_wave", "recommended": True,
             **_only("backup_type", "inverter", "ups")},
            {"attribute": "solar_input", "recommended": True,
             **_only("backup_type", "inverter")},
            {"attribute": "system_voltage", "recommended": True,
             **_only("backup_type", "inverter", "battery")},
            {"attribute": "battery_chemistry", "recommended": True,
             **_only("backup_type", "battery")},
            {"attribute": "capacity_ah", "recommended": True, "placeholder": "e.g. 200",
             **_only("backup_type", "battery")},
            WARRANTY,
        ],
    },
}
# fmt: on
