"""Vehicles & Auto: cars, motorcycles, bicycles, three-wheelers, heavy vehicles
and parts.

Same rules as electronics.py. The product's own `brand` and `name` carry the
make and model ("Toyota", "Vitz"), so they are not specs here; `condition`
(new / used) decides whether mileage is asked. `color` is shared with
electronics and defined there.
"""

from .electronics import ITEM, OPEN_LIST, USED, _opts

ATTRIBUTES = {
    # A model year, not `release_year`: that one starts at 1990, and older
    # vehicles are still bought and sold.
    "year": {"label": "Year", "type": "number", "min": 1950},
    "mileage": {
        "label": "Mileage",
        "type": "number",
        "unit": "km",
        "min": 0,
    },
    "transmission": {
        "label": "Transmission",
        "type": "enum",
        "options": _opts(("manual", "Manual"), ("automatic", "Automatic")),
    },
    "fuel": {
        "label": "Fuel",
        "type": "enum",
        "options": _opts(
            ("petrol", "Petrol"),
            ("diesel", "Diesel"),
            ("hybrid", "Hybrid"),
            ("plug_in_hybrid", "Plug-in hybrid"),
            ("electric", "Electric"),
            # Gas: LPG and CNG conversions on cars and Bajaj three-wheelers,
            # CNG and LNG on trucks and buses.
            ("lpg", "LPG"),
            ("cng", "CNG"),
            ("lng", "LNG"),
            ("other", "Other"),
        ),
    },
    "body_type": {
        "label": "Body type",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("sedan", "Sedan"),
            ("suv", "SUV"),
            ("hatchback", "Hatchback"),
            ("pickup", "Pickup"),
            ("van", "Van"),
            ("coupe", "Coupe"),
            ("station_wagon", "Station wagon"),
            ("minivan", "Minivan"),
            ("convertible", "Convertible"),
        ),
    },
    "engine_cc": {
        "label": "Engine",
        "type": "number",
        "unit": "cc",
        "min": 50,
    },
    "vehicle_kind": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("truck", "Truck"),
            ("bus", "Bus"),
            ("minibus", "Minibus"),
            ("machinery", "Machinery"),
            ("trailer", "Trailer"),
            ("other", "Other"),
        ),
    },
    "load_capacity": {
        "label": "Load capacity",
        "type": "number",
        "unit": "t",
        "min": 0.5,
    },
    "part_type": {
        "label": "Part",
        "type": "enum",
        "options": _opts(
            ("tyres", "Tyres"),
            ("battery", "Battery"),
            ("engine", "Engine parts"),
            ("body", "Body parts"),
            ("lights", "Lights"),
            ("interior", "Interior"),
            ("electronics", "Electronics"),
            ("other", "Other"),
        ),
    },
    # --- bicycles ---
    # Electric is a type, not a separate category: same frame, wheels and buyers.
    "bike_type": {
        "label": "Type",
        "type": "enum",
        "options": _opts(
            ("mountain", "Mountain"),
            ("road", "Road"),
            ("city", "City"),
            ("kids", "Kids"),
            ("electric", "Electric"),
            ("bmx", "BMX"),
            ("folding", "Folding"),
            ("other", "Other"),
        ),
    },
    "wheel_size": {
        "label": "Wheel size",
        "type": "number",
        "unit": "in",
        "min": 12,
    },
    "frame_size": {
        "label": "Frame size",
        "type": "enum",
        "options": _opts(("s", "S"), ("m", "M"), ("l", "L"), ("xl", "XL")),
    },
    "gears": {"label": "Gears", "type": "number", "min": 1},
    # Not `compatible_with`: that one suggests iPhone, Samsung Galaxy...
    # Typed freely: "Toyota Vitz 2010".
    "fits_vehicle": {"label": "Works with", "type": "text"},
    # --- what buyers ask next (see the category audit) ---
    "engine_size": {
        "label": "Engine size",
        "type": "number",
        "unit": "L",
        "min": 0.6,
    },
    "drivetrain": {
        "label": "Drive",
        "type": "enum",
        "options": _opts(("2wd", "2WD"), ("4wd", "4WD"), ("awd", "AWD")),
    },
    "motorcycle_type": {
        "label": "Type",
        "type": "text",
        # Pick or type: the list suggests, it does not limit.
        "convert_from": OPEN_LIST,
        "options": _opts(
            ("standard", "Standard"),
            ("sport", "Sport"),
            ("cruiser", "Cruiser"),
            ("scooter", "Scooter"),
            ("off_road", "Off-road"),
            ("touring", "Touring"),
            ("delivery", "Delivery / cargo"),
        ),
    },
    # An electric motor's rating, in kW as motors are sold and compared: a
    # 0.25 kW e-bike, a 2 kW scooter, a 150 kW car. An engine is sized in cc
    # or litres instead.
    "motor_power": {
        "label": "Motor power",
        "type": "number",
        "unit": "kW",
        "min": 0.05,
    },
    # The traction battery's energy, the electric counterpart to engine_cc.
    # Its own key: `battery_capacity` is mAh for phones and power banks, and a
    # unit lives on the shared attribute, so a vehicle cannot borrow it in kWh.
    # `min` is only a floor (0.1 kWh, a small e-bike); there is no upper bound,
    # so an e-bus or a future EV is never rejected.
    "battery_kwh": {
        "label": "Battery capacity",
        "type": "number",
        "unit": "kWh",
        "min": 0.1,
    },
    # Cars, buses and minibuses are sold by seat count.
    "seats": {"label": "Seats", "type": "number", "min": 1},
    "vehicle_use": {
        "label": "Use",
        "type": "enum",
        "options": _opts(("passenger", "Passenger"), ("cargo", "Cargo")),
    },
    # Machinery wears by engine hours, not kilometres.
    "operating_hours": {
        "label": "Operating hours",
        "type": "number",
        "unit": "h",
        "min": 0,
    },
    "brake_type": {
        "label": "Brakes",
        "type": "enum",
        "options": _opts(("disc", "Disc"), ("rim", "Rim")),
    },
    "range_km": {
        "label": "Range",
        "type": "number",
        "unit": "km",
        "min": 5,
    },
    # The first trust question when buying a part.
    "part_quality": {
        "label": "Part quality",
        "type": "enum",
        "options": _opts(
            ("genuine", "Genuine"),
            ("oem", "OEM"),
            ("aftermarket", "Aftermarket"),
        ),
    },
}

# fmt: off
YEAR = {"attribute": "year", "required": True, "placeholder": "e.g. 2015"}
TRANSMISSION = {"attribute": "transmission", "required": True}
FUEL = {"attribute": "fuel", "required": True}
ELECTRIC = {"fuel": ["electric"]}
# A vehicle with an engine, whatever it burns. An EV has no engine size or
# gearbox, and a single-speed reduction gear is not a transmission buyers ask about.
COMBUSTION = {"fuel": ["petrol", "diesel", "hybrid", "plug_in_hybrid", "lpg", "cng",
                      "lng"]}
# Charged from the wall: a plug-in hybrid's battery and electric range are
# what its buyers compare, as for an EV.
PLUG_IN = {"fuel": ["electric", "plug_in_hybrid"]}
# Small engines: a motorcycle or a Bajaj on petrol or gas.
SMALL_ENGINE = {"fuel": ["petrol", "lpg", "cng"]}
MILEAGE = {**ITEM, "attribute": "mileage", "show_if": USED, "required_if": USED,
           "placeholder": "e.g. 85000"}
# The vehicle colours from the shared list; phone finishes are left out.
VEHICLE_COLORS = ["black", "white", "silver", "gray", "blue", "red", "green",
                  "gold", "brown", "orange", "yellow"]


def _color(required=True):
    return {"attribute": "color", "required": required,
            "options": VEHICLE_COLORS, "placeholder": "e.g. White"}


PRODUCT_TYPES = {
    "car": {
        "label": "Car",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Corolla", "variant": "2017 \u00b7 1.6 L \u00b7 Automatic"},
        "categories": ["cars"],
        "brands": [
            "Toyota", "Suzuki", "Hyundai", "Nissan", "Mitsubishi", "Kia",
            "Volkswagen", "Mercedes-Benz", "BYD", "Isuzu",
        ],
        "fields": [
            {"attribute": "body_type", "required": True},
            YEAR,
            {**FUEL, "options": ["petrol", "diesel", "hybrid", "plug_in_hybrid",
                                 "electric", "lpg", "cng"]},
            {**TRANSMISSION, "required": False, "show_if": COMBUSTION,
             "required_if": COMBUSTION},
            {"attribute": "engine_size", "recommended": True,
             "show_if": COMBUSTION, "placeholder": "e.g. 1.5"},
            {"attribute": "motor_power", "recommended": True,
             "show_if": ELECTRIC, "placeholder": "e.g. 150"},
            {"attribute": "battery_kwh", "recommended": True,
             "show_if": PLUG_IN, "placeholder": "e.g. 60"},
            {"attribute": "range_km", "recommended": True,
             "show_if": PLUG_IN, "placeholder": "e.g. 420",
             "hint": "For a plug-in hybrid, the range on electricity alone."},
            {"attribute": "drivetrain", "recommended": True},
            {"attribute": "seats", "placeholder": "e.g. 5"},
            _color(),
            MILEAGE,
        ],
    },
    "motorcycle": {
        "label": "Motorcycle",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Boxer 150", "variant": "2024 \u00b7 Disc brake"},
        "categories": ["motorcycles"],
        "brands": ["Bajaj", "TVS", "Honda", "Yamaha", "Suzuki", "Yadea"],
        "fields": [
            {"attribute": "motorcycle_type", "required": True},
            # The power source decides the rest: an electric scooter has no
            # engine size or gearbox, but a motor rating, a battery and a range.
            {**FUEL, "label": "Power source", "options": ["petrol", "electric"]},
            YEAR,
            {"attribute": "engine_cc", "placeholder": "e.g. 150",
             "show_if": SMALL_ENGINE, "required_if": SMALL_ENGINE},
            {**TRANSMISSION, "required": False, "show_if": SMALL_ENGINE,
             "required_if": SMALL_ENGINE},
            {"attribute": "motor_power", "placeholder": "e.g. 2",
             "show_if": ELECTRIC, "required_if": ELECTRIC},
            {"attribute": "battery_kwh", "recommended": True,
             "placeholder": "e.g. 2.4", "show_if": ELECTRIC},
            {"attribute": "range_km", "recommended": True, "placeholder": "e.g. 70",
             "show_if": ELECTRIC},
            {**ITEM, "attribute": "battery_health", "recommended": True,
             "placeholder": "e.g. 92",
             "show_if": {"condition": ["used"], "fuel": ["electric"]}},
            {**ITEM, "attribute": "battery_cycles", "recommended": True,
             "placeholder": "e.g. 300",
             "show_if": {"condition": ["used"], "fuel": ["electric"]}},
            _color(),
            MILEAGE,
        ],
    },
    "bicycle": {
        "label": "Bicycle",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Talon 3", "variant": "29\" \u00b7 Size M"},
        "categories": ["bicycles"],
        "brands": [
            "Giant", "Trek", "Merida", "Specialized", "Cannondale", "Phoenix",
            "Hero", "Xiaomi",
        ],
        "fields": [
            {"attribute": "bike_type", "required": True, "control": "chips"},
            {"attribute": "wheel_size", "required": True, "placeholder": "e.g. 26"},
            {"attribute": "frame_size"},
            {"attribute": "gears", "placeholder": "e.g. 21"},
            {"attribute": "brake_type"},
            {"attribute": "motor_power", "placeholder": "e.g. 0.25",
             "show_if": {"bike_type": ["electric"]},
             "required_if": {"bike_type": ["electric"]}},
            {"attribute": "battery_kwh", "recommended": True, "placeholder": "e.g. 0.5",
             "show_if": {"bike_type": ["electric"]}},
            {"attribute": "range_km", "recommended": True, "placeholder": "e.g. 60",
             "show_if": {"bike_type": ["electric"]}},
            _color(),
        ],
    },
    "three_wheeler": {
        "label": "Three-wheeler",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "RE 4S", "variant": "2023 \u00b7 Passenger"},
        "categories": ["three-wheelers"],
        "brands": ["Bajaj", "TVS", "Piaggio"],
        "fields": [
            {"attribute": "vehicle_use", "recommended": True},
            {**FUEL, "label": "Power source",
             "options": ["petrol", "electric", "lpg", "cng"]},
            YEAR,
            {"attribute": "engine_cc", "placeholder": "e.g. 200",
             "show_if": SMALL_ENGINE, "required_if": SMALL_ENGINE},
            {**TRANSMISSION, "required": False, "show_if": SMALL_ENGINE,
             "required_if": SMALL_ENGINE},
            {"attribute": "motor_power", "placeholder": "e.g. 1.2",
             "show_if": ELECTRIC, "required_if": ELECTRIC},
            {"attribute": "battery_kwh", "recommended": True, "placeholder": "e.g. 3",
             "show_if": ELECTRIC},
            _color(),
            MILEAGE,
        ],
    },
    "heavy_vehicle": {
        "label": "Truck & heavy vehicle",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "FSR", "variant": "2019 \u00b7 8-ton truck"},
        "categories": ["trucks-heavy"],
        "brands": [
            "Isuzu", "Sinotruk", "Mercedes-Benz", "Volvo", "Iveco", "FAW", "Toyota",
        ],
        "fields": [
            {"attribute": "vehicle_kind", "required": True},
            YEAR,
            TRANSMISSION,
            {**FUEL, "options": ["diesel", "petrol", "hybrid", "electric", "cng", "lng"]},
            {"attribute": "load_capacity", "placeholder": "e.g. 10",
             "show_if": {"vehicle_kind": ["truck"]}},
            {"attribute": "seats", "placeholder": "e.g. 30",
             "show_if": {"vehicle_kind": ["bus", "minibus"]}},
            {"attribute": "motor_power", "recommended": True, "placeholder": "e.g. 250",
             "show_if": ELECTRIC},
            {"attribute": "battery_kwh", "recommended": True, "placeholder": "e.g. 280",
             "show_if": ELECTRIC},
            {"attribute": "range_km", "recommended": True, "placeholder": "e.g. 250",
             "show_if": ELECTRIC},
            _color(required=False),
            {**MILEAGE, "show_if": {**USED, "vehicle_kind": ["truck", "bus", "minibus"]},
             "required_if": {**USED, "vehicle_kind": ["truck", "bus", "minibus"]}},
            {**ITEM, "attribute": "operating_hours", "placeholder": "e.g. 4200",
             "show_if": {**USED, "vehicle_kind": ["machinery"]},
             "required_if": {**USED, "vehicle_kind": ["machinery"]}},
        ],
    },
    "auto_part": {
        "label": "Auto part",
        # What the listing form suggests for the name and variant.
        "listing_examples": {"name": "Ecopia EP150 Tyre", "variant": "185/65 R15"},
        "categories": ["auto-parts"],
        "brands": [],
        "fields": [
            {"attribute": "part_type", "required": True, "control": "select"},
            {"attribute": "part_quality", "recommended": True},
            {"attribute": "fits_vehicle", "placeholder": "e.g. Toyota Vitz 2010"},
        ],
    },
}
# fmt: on
