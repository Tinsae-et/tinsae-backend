"""Fashion & Apparel: men's, women's and kids' clothing, shoes, bags, watches
and jewelry.

Same rules as electronics.py. Clothing is sold in a run of sizes and colours,
so `sizes`, `shoe_size`, `kids_size` and `color` are `multiple` here: one
listing says "S to XL, black or navy", and a buyer filtering on M finds it.
Stock is availability only, not per size. Much of it is unbranded or locally
made, so `brand_required` is off everywhere. `color` and `material` are shared
attributes defined elsewhere; the fashion options are added there.
"""

from .electronics import _opts

ATTRIBUTES = {
    # --- clothing ---
    "clothing_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("tshirt", "T-shirt"),
            ("shirt", "Shirt"),
            ("polo", "Polo"),
            ("trousers", "Trousers / Jeans"),
            ("dress", "Dress"),
            ("skirt", "Skirt"),
            ("jacket", "Jacket / Coat"),
            ("suit", "Suit / Blazer"),
            ("sweater", "Sweater / Hoodie"),
            ("sportswear", "Sportswear"),
            # Habesha kemis, gabi, netela, kuta: a category of its own in Addis.
            ("traditional", "Traditional (Habesha)"),
            ("other", "Other"),
        ),
    },
    # Pick or type: "32/34" or "EU 50" are real sizes too.
    "sizes": {
        "label": "Sizes",
        "type": "text",
        "options": _opts(
            ("xs", "XS"),
            ("s", "S"),
            ("m", "M"),
            ("l", "L"),
            ("xl", "XL"),
            ("xxl", "XXL"),
            ("xxxl", "XXXL"),
            ("one_size", "One size"),
        ),
    },
    # Kids' clothes are sized by age, not letters.
    "kids_size": {
        "label": "Sizes (age)",
        "type": "text",
        "options": _opts(
            ("0_3m", "0–3 months"),
            ("3_6m", "3–6 months"),
            ("6_12m", "6–12 months"),
            ("1_2y", "1–2 years"),
            ("2_3y", "2–3 years"),
            ("3_4y", "3–4 years"),
            ("4_5y", "4–5 years"),
            ("6_7y", "6–7 years"),
            ("8_9y", "8–9 years"),
            ("10_11y", "10–11 years"),
            ("12_14y", "12–14 years"),
        ),
    },
    "fit": {
        "label": "Fit",
        "type": "enum",
        "options": _opts(
            ("slim", "Slim"), ("regular", "Regular"), ("oversized", "Oversized")
        ),
    },
    # Who it is for, where the category does not already say.
    "gender": {
        "label": "For",
        "type": "enum",
        "options": _opts(
            ("men", "Men"),
            ("women", "Women"),
            ("unisex", "Unisex"),
            ("kids", "Kids"),
            ("boy", "Boy"),
            ("girl", "Girl"),
        ),
    },
    # Copies are common; saying so up front builds trust, and buyers filter on it.
    "authenticity": {
        "label": "Authenticity",
        "type": "enum",
        "options": _opts(("original", "Original"), ("replica", "Replica / copy")),
    },
    # --- shoes ---
    "shoe_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("sneakers", "Sneakers"),
            ("formal", "Formal"),
            ("boots", "Boots"),
            ("sandals", "Sandals / Slippers"),
            ("heels", "Heels"),
            ("sports", "Sports / Running"),
            ("other", "Other"),
        ),
    },
    # EU sizes, as sold in Addis; anything else can be typed.
    "shoe_size": {
        "label": "Sizes (EU)",
        "type": "text",
        "options": _opts(*((str(n), str(n)) for n in range(35, 47))),
    },
    # --- bags ---
    "bag_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("backpack", "Backpack"),
            ("handbag", "Handbag"),
            ("crossbody", "Shoulder / Crossbody"),
            ("wallet", "Wallet / Purse"),
            ("laptop_bag", "Laptop bag"),
            ("luggage", "Travel / Luggage"),
            ("other", "Other"),
        ),
    },
    # --- watches & jewelry ---
    "jewelry_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("watch", "Watch"),
            ("necklace", "Necklace"),
            ("ring", "Ring"),
            ("bracelet", "Bracelet"),
            ("earrings", "Earrings"),
            ("other", "Other"),
        ),
    },
    "metal": {
        "label": "Material",
        "type": "enum",
        "options": _opts(
            ("gold", "Gold"),
            ("silver", "Silver"),
            ("gold_plated", "Gold-plated"),
            ("stainless", "Stainless steel"),
            ("leather", "Leather"),
            ("other", "Other"),
        ),
    },
    # Gold is priced by purity and weight: the first two questions.
    "karat": {
        "label": "Karat",
        "type": "enum",
        "options": _opts(
            ("18k", "18K"), ("21k", "21K"), ("22k", "22K"), ("24k", "24K")
        ),
    },
    "weight_g": {
        "label": "Weight",
        "type": "number",
        "unit": "g",
        "min": 0.1,
    },
    "movement": {
        "label": "Movement",
        "type": "enum",
        "options": _opts(("quartz", "Quartz (battery)"), ("automatic", "Automatic")),
    },
}

# Shared-attribute options these types use (the rest are phone finishes).
FASHION_COLORS = [
    "black",
    "white",
    "gray",
    "navy",
    "blue",
    "green",
    "red",
    "pink",
    "purple",
    "yellow",
    "orange",
    "brown",
    "beige",
    "gold",
    "silver",
    "multicolor",
]
CLOTHING_MATERIALS = [
    "cotton",
    "polyester",
    "denim",
    "linen",
    "wool",
    "leather",
    "blend",
]
SHOE_MATERIALS = ["leather", "synthetic", "canvas", "suede"]
BAG_MATERIALS = ["leather", "synthetic", "canvas", "fabric"]
# Every clothing type but those men do not buy; kids skip suits and polos.
MEN_TYPES = [
    "tshirt",
    "shirt",
    "polo",
    "trousers",
    "jacket",
    "suit",
    "sweater",
    "sportswear",
    "traditional",
    "other",
]
KIDS_TYPES = [
    "tshirt",
    "shirt",
    "trousers",
    "dress",
    "skirt",
    "jacket",
    "sweater",
    "sportswear",
    "traditional",
    "other",
]

# fmt: off
def _only(field, *values):
    """Shown, and filterable, only for these values of a type field."""
    return {"show_if": {field: list(values)}}


COLORS = {"attribute": "color", "recommended": True, "multiple": True,
          "options": FASHION_COLORS, "placeholder": "Add a colour",
          "hint": "Every colour you have in stock."}
AUTHENTIC = {"attribute": "authenticity", "recommended": True,
             "hint": "Say so if it is a copy: honest listings get fewer disputes."}
CLOTHING_BRANDS = ["Zara", "H&M", "Nike", "Adidas", "Puma", "Levi's", "Tommy Hilfiger",
                   "Lacoste", "Shein", "LC Waikiki", "Defacto"]


def _clothing(label, category, types, example, sizes="sizes", extra=()):
    return {
        "label": label,
        "listing_examples": example,
        "categories": [category],
        "brand_required": False,
        "brands": CLOTHING_BRANDS,
        "fields": [
            {"attribute": "clothing_type", "required": True,
             **({"options": types} if types else {})},
            *extra,
            {"attribute": sizes, "required": True, "multiple": True,
             "placeholder": "Add a size", "hint": "Every size you have in stock."},
            COLORS,
            {"attribute": "material", "recommended": True,
             "options": CLOTHING_MATERIALS, "placeholder": "e.g. Cotton"},
            {"attribute": "fit", **_only("clothing_type", "tshirt", "shirt", "polo",
                                         "trousers", "jacket", "suit", "sweater")},
        ],
    }


PRODUCT_TYPES = {
    "mens_clothing": _clothing(
        "Men's clothing", "mens-clothing", MEN_TYPES,
        {"name": "Slim Fit Oxford Shirt", "variant": "Long sleeve · Cotton"},
    ),
    "womens_clothing": _clothing(
        "Women's clothing", "womens-clothing", None,
        {"name": "Habesha Kemis", "variant": "Tilet border · Hand-woven"},
    ),
    "kids_clothing": _clothing(
        "Kids' clothing", "kids-clothing", KIDS_TYPES,
        {"name": "School Uniform Set", "variant": "Shirt + trousers"},
        sizes="kids_size",
        extra=[{"attribute": "gender", "recommended": True,
                "options": ["boy", "girl", "unisex"]}],
    ),
    "shoes": {
        "label": "Shoes",
        "listing_examples": {"name": "Air Force 1 '07", "variant": "White · Leather"},
        "categories": ["shoes"],
        "brand_required": False,
        "brands": ["Nike", "Adidas", "Puma", "New Balance", "Converse", "Vans",
                   "Skechers", "Clarks", "Timberland"],
        "fields": [
            {"attribute": "shoe_type", "required": True},
            {"attribute": "gender", "required": True,
             "options": ["men", "women", "unisex", "kids"]},
            {"attribute": "shoe_size", "required": True, "multiple": True,
             "placeholder": "Add a size", "hint": "Every size you have in stock."},
            COLORS,
            {"attribute": "material", "recommended": True, "options": SHOE_MATERIALS,
             "placeholder": "e.g. Leather"},
            AUTHENTIC,
        ],
    },
    "bags": {
        "label": "Bags",
        "listing_examples": {"name": "Leather Tote Bag", "variant": "Large · Brown"},
        "categories": ["bags"],
        "brand_required": False,
        "brands": ["Michael Kors", "Guess", "Coach", "Samsonite", "American Tourister",
                   "JanSport", "Nike", "Adidas"],
        "fields": [
            {"attribute": "bag_type", "required": True},
            {"attribute": "gender", "recommended": True,
             "options": ["men", "women", "unisex", "kids"]},
            COLORS,
            {"attribute": "material", "recommended": True, "options": BAG_MATERIALS,
             "placeholder": "e.g. Leather"},
            AUTHENTIC,
        ],
    },
    "watches_jewelry": {
        "label": "Watches & jewelry",
        "listing_examples": {"name": "21K Gold Habesha Necklace", "variant": "Meskel pendant"},
        "categories": ["watches-jewelry"],
        "brand_required": False,
        "brands": ["Casio", "Seiko", "Citizen", "Fossil", "Tissot", "Rolex",
                   "Michael Kors", "Pandora"],
        "fields": [
            {"attribute": "jewelry_type", "required": True},
            {"attribute": "gender", "recommended": True,
             "options": ["men", "women", "unisex"]},
            {"attribute": "metal", "required": True},
            {"attribute": "karat", **_only("metal", "gold"),
             "required_if": {"metal": ["gold"]}},
            {"attribute": "weight_g", "placeholder": "e.g. 12.5",
             **_only("metal", "gold", "silver"), "required_if": {"metal": ["gold"]},
             "hint": "Gold and silver are priced by weight."},
            {"attribute": "movement", "recommended": True,
             **_only("jewelry_type", "watch")},
            {**AUTHENTIC, **_only("jewelry_type", "watch")},
        ],
    },
}
# fmt: on
