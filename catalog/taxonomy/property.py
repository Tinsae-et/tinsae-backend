"""Property & Rentals: apartments, houses, land and commercial space.

Same rules as electronics.py, with three differences that the listing form and
the product pages read from the type (presentation, so not synced):

- `has_condition: False`: "New / Used / Refurbished" describes goods, not a
  house; whether it is newly built is a spec instead.
- `brand_label`: the brand box asks for the developer ("Noah Real Estate").
- every listing is for sale or for rent (`listing_type`), and a rent price is
  per month; the pages print "/ month" after it.

Location is the sub-city plus the area typed as people say it ("CMC",
"Ayat"), so buyers can filter by sub-city and still read the exact spot.
"""

from .electronics import OPEN_LIST, _opts

ATTRIBUTES = {
    "listing_type": {
        "label": "For sale or rent",
        "type": "enum",
        "options": _opts(("sale", "For sale"), ("rent", "For rent")),
    },
    "subcity": {
        "label": "Sub-city",
        "type": "enum",
        "options": _opts(
            ("bole", "Bole"),
            ("yeka", "Yeka"),
            ("kirkos", "Kirkos"),
            ("arada", "Arada"),
            ("lideta", "Lideta"),
            ("addis_ketema", "Addis Ketema"),
            ("gulele", "Gulele"),
            ("kolfe_keranio", "Kolfe Keranio"),
            ("nifas_silk_lafto", "Nifas Silk-Lafto"),
            ("akaky_kaliti", "Akaky Kaliti"),
            ("lemi_kura", "Lemi Kura"),
            ("outside_addis", "Outside Addis Ababa"),
        ),
    },
    # Typed, because neighbourhood names are what people search and say.
    "area_name": {"label": "Area", "type": "text"},
    "apartment_type": {
        "label": "Type",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("apartment", "Apartment"),
            ("condominium", "Condominium"),
            ("penthouse", "Penthouse"),
            ("duplex", "Duplex"),
        ),
    },
    "bedrooms": {"label": "Bedrooms", "type": "number", "min": 0, "max": 20},
    "bathrooms": {"label": "Bathrooms", "type": "number", "min": 1, "max": 20},
    "size_m2": {"label": "Size", "type": "number", "unit": "m²", "min": 1},
    "plot_m2": {"label": "Plot size", "type": "number", "unit": "m²", "min": 1},
    "floor": {"label": "Floor", "type": "number", "min": 0, "max": 100},
    # How Addis describes a house: G+1 is a ground floor and one above.
    "storeys": {
        "label": "Storeys",
        "type": "enum",
        "options": _opts(
            ("g0", "Ground only (G+0)"),
            ("g1", "G+1"),
            ("g2", "G+2"),
            ("g3", "G+3"),
            ("g4_plus", "G+4 or more"),
        ),
    },
    "furnishing": {
        "label": "Furnishing",
        "type": "enum",
        "options": _opts(
            ("furnished", "Furnished"),
            ("semi", "Semi-furnished"),
            ("unfurnished", "Unfurnished"),
        ),
    },
    "new_build": {
        "label": "Newly built",
        "type": "enum",
        "options": _opts(("yes", "Yes, never lived in"), ("no", "No")),
    },
    "amenities": {
        "label": "Amenities",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("parking", "Parking"),
            ("elevator", "Elevator"),
            ("water_tank", "Water tank"),
            ("backup_power", "Backup power"),
            ("security", "24h security"),
            ("garden", "Garden / compound"),
            ("swimming_pool", "Swimming pool"),
            ("gym", "Gym"),
        ),
    },
    # Whether the seller can hand over the ownership certificate ("carta"):
    # the first question of anyone buying property in Ethiopia.
    "title_deed": {
        "label": "Title deed",
        "type": "enum",
        "options": _opts(
            ("ready", "Title deed ready"),
            ("in_process", "In process"),
            ("none", "No title deed"),
        ),
    },
    "advance_months": {
        "label": "Paid upfront",
        "type": "number",
        "unit": "months",
        "min": 0,
        "max": 24,
    },
    "land_use": {
        "label": "Land use",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("residential", "Residential"),
            ("commercial", "Commercial"),
            ("mixed", "Mixed use"),
            ("agricultural", "Agricultural"),
            ("industrial", "Industrial"),
        ),
    },
    "commercial_type": {
        "label": "Type",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("office", "Office"),
            ("shop", "Shop / retail"),
            ("warehouse", "Warehouse"),
            ("building", "Whole building"),
        ),
    },
}

# fmt: off
SALE = {"listing_type": ["sale"]}
RENT = {"listing_type": ["rent"]}

LISTING_TYPE = {
    "attribute": "listing_type", "required": True,
    "hint": "For rent, the price is per month.",
}
SUBCITY = {"attribute": "subcity", "required": True}
AREA = {
    "attribute": "area_name", "required": True, "placeholder": "e.g. CMC, Ayat, Summit",
    "hint": "The neighbourhood, as people know it.",
}
TITLE_DEED = {
    "attribute": "title_deed", "recommended": True, "show_if": SALE,
    "hint": "Buyers ask first whether the ownership certificate (carta) can be handed over.",
}
ADVANCE = {
    "attribute": "advance_months", "recommended": True, "show_if": RENT,
    "placeholder": "e.g. 3",
    "hint": "How many months' rent the tenant pays before moving in.",
}
FURNISHING = {"attribute": "furnishing", "recommended": True}
AMENITIES = {"attribute": "amenities", "recommended": True, "multiple": True}
DEVELOPERS = [
    "Noah Real Estate", "Ayat Real Estate", "Gift Real Estate", "Sunshine Real Estate",
    "Flintstone Homes", "Temer Properties", "Enyi Real Estate",
]
# Presentation every property type shares (see the module docstring).
PROPERTY = {"has_condition": False, "brand_required": False,
            "brand_label": "Developer"}


PRODUCT_TYPES = {
    "apartment": {
        **PROPERTY,
        "label": "Apartment",
        "listing_examples": {"name": "3-Bedroom Apartment", "variant": "140 m² · 5th floor"},
        "categories": ["apartments"],
        "brands": DEVELOPERS,
        "fields": [
            LISTING_TYPE,
            {"attribute": "apartment_type", "required": True},
            SUBCITY,
            AREA,
            {"attribute": "bedrooms", "required": True, "placeholder": "e.g. 3",
             "hint": "0 for a studio."},
            {"attribute": "bathrooms", "recommended": True, "placeholder": "e.g. 2"},
            {"attribute": "size_m2", "required": True, "placeholder": "e.g. 140"},
            {"attribute": "floor", "recommended": True, "placeholder": "e.g. 5",
             "hint": "0 for the ground floor."},
            FURNISHING,
            AMENITIES,
            {"attribute": "new_build", "recommended": True},
            TITLE_DEED,
            ADVANCE,
        ],
    },
    "house": {
        **PROPERTY,
        "label": "House",
        "listing_examples": {"name": "G+1 Villa with Compound", "variant": "5 bedrooms · 500 m² plot"},
        "categories": ["houses"],
        "brands": DEVELOPERS,
        "fields": [
            LISTING_TYPE,
            SUBCITY,
            AREA,
            {"attribute": "storeys", "required": True},
            {"attribute": "bedrooms", "required": True, "placeholder": "e.g. 4"},
            {"attribute": "bathrooms", "recommended": True, "placeholder": "e.g. 3"},
            {"attribute": "size_m2", "label": "Built size", "recommended": True,
             "placeholder": "e.g. 320"},
            {"attribute": "plot_m2", "required": True, "placeholder": "e.g. 500"},
            FURNISHING,
            AMENITIES,
            {"attribute": "new_build", "recommended": True},
            TITLE_DEED,
            ADVANCE,
        ],
    },
    "land": {
        **PROPERTY,
        "label": "Land",
        "listing_examples": {"name": "Residential Plot", "variant": "250 m² · Lease"},
        "categories": ["land"],
        "brands": [],
        "fields": [
            LISTING_TYPE,
            SUBCITY,
            AREA,
            {"attribute": "plot_m2", "required": True, "placeholder": "e.g. 250"},
            {"attribute": "land_use", "required": True},
            # A plot without a deed is a very different purchase.
            {**TITLE_DEED, "recommended": False, "required_if": SALE},
            ADVANCE,
        ],
    },
    "commercial_property": {
        **PROPERTY,
        "label": "Commercial property",
        "listing_examples": {"name": "Office Space on Bole Road", "variant": "220 m² · 3rd floor"},
        "categories": ["commercial-property"],
        "brands": DEVELOPERS,
        "fields": [
            LISTING_TYPE,
            {"attribute": "commercial_type", "required": True},
            SUBCITY,
            AREA,
            {"attribute": "size_m2", "required": True, "placeholder": "e.g. 220"},
            {"attribute": "floor", "recommended": True, "placeholder": "e.g. 3",
             "hint": "0 for the ground floor."},
            FURNISHING,
            AMENITIES,
            TITLE_DEED,
            ADVANCE,
        ],
    },
}
# fmt: on
