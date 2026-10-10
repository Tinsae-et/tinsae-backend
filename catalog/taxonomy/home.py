"""Furniture & Appliances: furniture, beds and mattresses, appliances, kitchen and
dining, décor and lighting.

Same rules as electronics.py. Most furniture, bedding and décor in Addis is
made locally or sold unbranded, so those types set `brand_required: False`;
appliances keep it, because a fridge is bought by its brand. `color`,
`seats` and `warranty` are shared attributes defined elsewhere.
"""

from .electronics import ITEM, WARRANTY, _opts

ATTRIBUTES = {
    # --- furniture ---
    "furniture_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("sofa", "Sofa"),
            ("dining_table", "Dining table"),
            ("coffee_table", "Coffee table"),
            ("wardrobe", "Wardrobe"),
            ("desk", "Desk"),
            ("chair", "Chair"),
            ("shelf_tv_stand", "Shelf / TV stand"),
            ("other", "Other"),
        ),
    },
    # Pick or type: "Mahogany", "Zigba", "Rattan" are real answers too.
    "material": {
        "label": "Material",
        "type": "text",
        "options": _opts(
            ("solid_wood", "Solid wood"),
            ("mdf", "MDF"),
            ("metal", "Metal"),
            ("leather", "Leather"),
            ("fabric", "Fabric"),
            ("glass", "Glass"),
            # Clothing, shoes and bags (catalog/taxonomy/fashion.py).
            ("cotton", "Cotton"),
            ("polyester", "Polyester"),
            ("denim", "Denim"),
            ("linen", "Linen"),
            ("wool", "Wool"),
            ("blend", "Blend"),
            ("synthetic", "Synthetic"),
            ("canvas", "Canvas"),
            ("suede", "Suede"),
        ),
    },
    # Whether it fits the room is the first question for any furniture.
    "dimensions": {"label": "Size (W × D × H)", "type": "text"},
    "delivery": {
        "label": "Delivery",
        "type": "enum",
        "options": _opts(
            ("free_addis", "Free in Addis"),
            ("paid", "Paid delivery"),
            ("pickup", "Pickup only"),
        ),
    },
    # --- beds & mattresses ---
    "bed_item_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("bed_frame", "Bed frame"),
            ("mattress", "Mattress"),
            ("bed_sheets", "Bed sheets"),
            ("duvet", "Blanket / Duvet"),
            ("pillow", "Pillow"),
            ("other", "Other"),
        ),
    },
    "bed_size": {
        "label": "Size",
        "type": "enum",
        "options": _opts(
            ("single", "Single"),
            ("double", "Double"),
            ("queen", "Queen"),
            ("king", "King"),
        ),
    },
    "mattress_type": {
        "label": "Mattress type",
        "type": "enum",
        "options": _opts(
            ("foam", "Foam"),
            ("spring", "Spring"),
            ("pocket_spring", "Pocket spring"),
            ("hybrid", "Hybrid"),
        ),
    },
    "thickness_cm": {
        "label": "Thickness",
        "type": "number",
        "unit": "cm",
        "min": 2,
    },
    "firmness": {
        "label": "Firmness",
        "type": "enum",
        "options": _opts(("soft", "Soft"), ("medium", "Medium"), ("firm", "Firm")),
    },
    # --- appliances ---
    "appliance_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("refrigerator", "Refrigerator"),
            ("washing_machine", "Washing machine"),
            ("stove_oven", "Stove / Oven"),
            ("microwave", "Microwave"),
            ("water_dispenser", "Water dispenser"),
            ("air_conditioner", "Air conditioner"),
            ("water_heater", "Water heater"),
            ("fan", "Fan"),
            ("iron", "Iron"),
            ("vacuum_cleaner", "Vacuum cleaner"),
            ("other", "Other"),
        ),
    },
    "fridge_style": {
        "label": "Fridge type",
        "type": "enum",
        "options": _opts(
            ("single_door", "Single door"),
            ("top_freezer", "Top freezer"),
            ("bottom_freezer", "Bottom freezer"),
            ("side_by_side", "Side-by-side"),
            ("chest_freezer", "Chest freezer"),
        ),
    },
    "capacity_l": {
        "label": "Capacity",
        "type": "number",
        "unit": "L",
        "min": 10,
    },
    "washer_style": {
        "label": "Loading",
        "type": "enum",
        "options": _opts(
            ("front_load", "Front load"),
            ("top_load", "Top load"),
            ("twin_tub", "Twin tub"),
        ),
    },
    "load_kg": {"label": "Load", "type": "number", "unit": "kg", "min": 1},
    "cooking_fuel": {
        "label": "Fuel",
        "type": "enum",
        "options": _opts(
            ("electric", "Electric"),
            ("gas", "Gas"),
            ("gas_electric", "Gas + electric"),
        ),
    },
    # An AC is sized to the room by its cooling power.
    "cooling_btu": {
        "label": "Cooling",
        "type": "number",
        "unit": "BTU",
        "min": 5000,
    },
    # Inverter models use far less power: a running-cost question.
    "inverter": {
        "label": "Inverter",
        "type": "enum",
        "options": _opts(("yes", "Yes"), ("no", "No")),
    },
    # --- kitchen & dining ---
    "kitchen_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("cookware", "Cookware"),
            ("electric_mitad", "Electric mitad"),
            ("blender_mixer", "Blender / Mixer"),
            ("kettle", "Kettle"),
            ("dinnerware", "Dinnerware"),
            ("utensils", "Utensils"),
            ("other", "Other"),
        ),
    },
    "kitchen_material": {
        "label": "Material",
        "type": "text",
        "options": _opts(
            ("stainless_steel", "Stainless steel"),
            ("non_stick", "Non-stick"),
            ("ceramic", "Ceramic"),
            ("glass", "Glass"),
            ("plastic", "Plastic"),
            ("clay", "Clay"),
        ),
    },
    "set_pieces": {"label": "Pieces in set", "type": "number", "min": 1},
    # A mitad's size is how many injera fit, and so who it is for.
    "diameter_cm": {
        "label": "Diameter",
        "type": "number",
        "unit": "cm",
        "min": 10,
    },
    # --- décor & lighting ---
    "decor_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("lighting", "Lighting"),
            ("rugs", "Rugs & carpets"),
            ("curtains", "Curtains"),
            ("mirrors", "Mirrors"),
            ("wall_art", "Wall art"),
            ("plants_pots", "Plants & pots"),
            ("other", "Other"),
        ),
    },
    "light_type": {
        "label": "Light type",
        "type": "enum",
        "options": _opts(
            ("ceiling", "Ceiling"),
            ("table_lamp", "Table lamp"),
            ("floor_lamp", "Floor lamp"),
            ("wall_light", "Wall light"),
            ("chandelier", "Chandelier"),
        ),
    },
}

# fmt: off
# The furniture colours; phone finishes like "Titanium" make no sense here.
HOME_COLORS = ["black", "white", "gray", "brown", "blue", "green", "red",
               "yellow", "orange", "gold", "silver", "pink", "purple"]


def _color(required=False):
    return {"attribute": "color", "required": required,
            "options": HOME_COLORS, "placeholder": "e.g. Gray"}


def _only(field, *values):
    """Shown, and filterable, only for these values of a type field."""
    return {"show_if": {field: list(values)}}


PRODUCT_TYPES = {
    "furniture": {
        "label": "Furniture",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "3-Seater Leather Sofa", "variant": "Brown \u00b7 210 cm"},
        "categories": ["furniture"],
        "brand_required": False,
        "brands": [],
        "fields": [
            {"attribute": "furniture_type", "required": True},
            {"attribute": "material", "required": True,
             "placeholder": "e.g. Solid wood or Leather"},
            {"attribute": "seats", "recommended": True, "placeholder": "e.g. 3",
             **_only("furniture_type", "sofa", "dining_table")},
            {"attribute": "dimensions", "recommended": True,
             "placeholder": "e.g. 210 × 90 × 85 cm",
             "hint": "Width × depth × height, so buyers know it fits."},
            _color(),
            {**ITEM, "attribute": "delivery"},
        ],
    },
    "bed_mattress": {
        "label": "Beds & mattresses",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Pocket Spring Mattress", "variant": "Queen \u00b7 25 cm"},
        "categories": ["beds-mattresses"],
        "brand_required": False,
        "brands": [],
        "fields": [
            {"attribute": "bed_item_type", "required": True},
            # Everything but a pillow comes in a bed size.
            {"attribute": "bed_size",
             **_only("bed_item_type", "bed_frame", "mattress", "bed_sheets", "duvet"),
             "required_if": {"bed_item_type": ["bed_frame", "mattress", "bed_sheets",
                                               "duvet"]}},
            {"attribute": "mattress_type", "recommended": True,
             **_only("bed_item_type", "mattress")},
            {"attribute": "thickness_cm", "recommended": True, "placeholder": "e.g. 25",
             **_only("bed_item_type", "mattress")},
            {"attribute": "firmness", **_only("bed_item_type", "mattress")},
            {"attribute": "material", "placeholder": "e.g. Solid wood",
             **_only("bed_item_type", "bed_frame")},
            _color(),
            {**ITEM, "attribute": "delivery"},
        ],
    },
    "appliance": {
        "label": "Appliance",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "350L Top Freezer Fridge", "variant": "Silver \u00b7 Inverter"},
        "categories": ["appliances"],
        "brands": [
            "Samsung", "LG", "Hisense", "Haier", "Midea", "TCL", "Beko",
            "Philips", "Kenwood", "Bosch",
        ],
        "fields": [
            {"attribute": "appliance_type", "required": True},
            {"attribute": "fridge_style", "recommended": True,
             **_only("appliance_type", "refrigerator")},
            {"attribute": "capacity_l", "recommended": True, "placeholder": "e.g. 350",
             **_only("appliance_type", "refrigerator", "water_heater")},
            {"attribute": "washer_style", "recommended": True,
             **_only("appliance_type", "washing_machine")},
            {"attribute": "load_kg", "recommended": True, "placeholder": "e.g. 8",
             **_only("appliance_type", "washing_machine")},
            {"attribute": "cooking_fuel", "recommended": True,
             **_only("appliance_type", "stove_oven")},
            {"attribute": "cooling_btu", "recommended": True, "placeholder": "e.g. 12000",
             **_only("appliance_type", "air_conditioner")},
            {"attribute": "inverter",
             **_only("appliance_type", "refrigerator", "washing_machine",
                     "air_conditioner")},
            _color(),
            WARRANTY,
        ],
    },
    "kitchen": {
        "label": "Kitchen & dining",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Electric Mitad", "variant": "55 cm"},
        "categories": ["kitchen-dining"],
        "brand_required": False,
        "brands": ["Tefal", "Prestige", "Philips", "Kenwood", "Moulinex", "Pyrex"],
        "fields": [
            {"attribute": "kitchen_type", "required": True},
            {"attribute": "kitchen_material", "placeholder": "e.g. Stainless steel",
             **_only("kitchen_type", "cookware", "dinnerware", "utensils")},
            {"attribute": "set_pieces", "placeholder": "e.g. 12",
             **_only("kitchen_type", "cookware", "dinnerware", "utensils")},
            {"attribute": "diameter_cm", "recommended": True, "placeholder": "e.g. 55",
             **_only("kitchen_type", "electric_mitad")},
            _color(),
            # Electric items only: a pot has no warranty worth asking about.
            {**WARRANTY, "required": False,
             **_only("kitchen_type", "electric_mitad", "blender_mixer", "kettle")},
        ],
    },
    "decor": {
        "label": "Décor & lighting",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Ceramic Table Lamp", "variant": "White \u00b7 45 cm"},
        "categories": ["decor-lighting"],
        "brand_required": False,
        "brands": [],
        "fields": [
            {"attribute": "decor_type", "required": True},
            {"attribute": "light_type", "recommended": True,
             **_only("decor_type", "lighting")},
            {"attribute": "dimensions", "placeholder": "e.g. 160 × 230 cm",
             **_only("decor_type", "rugs", "curtains", "mirrors", "wall_art")},
            _color(),
        ],
    },
}
# fmt: on
