import io
import urllib.request
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, ImageDraw

from catalog.models import (
    Category,
    Product,
    ProductCondition,
    ProductImage,
    ProductStatus,
)
from catalog.seed_specs import ensure_taxonomy, seed_specs
from shops.models import Shop
from users.models import User

# Confirmation age buckets for realistic freshness distribution.
SEED_CONFIRMATION_AGE = {
    "fresh": timedelta(hours=3),
    "almost_expired": timedelta(days=4),
    "expired": timedelta(days=45),
}

# Hard ceiling on any single seeded image. Nothing downstream re-checks these
# bytes: they are written straight into MEDIA_ROOT, and the seeder builds
# ProductImage directly rather than going through an upload, so neither
# `validate_image_file` (5 MB) nor the serializers ever see them.
SEED_IMAGE_MAX_BYTES = 100 * 1024

# The shots one seeded product gets, in display order. The first is the primary
# and the only one that may come from the network; the rest are drawn locally so
# a gallery shows one device from several angles rather than four unrelated
# stock photos of four different devices.
PRODUCT_IMAGE_VIEWS = ["front", "back", "detail", "box"]

SEED_USER = {
    "username": "seed_merchant",
    "email": "merchant@tinsae.et",
    "password": "seedpass123",
}

# 8 realistic Ethiopian electronics retailers across Addis Ababa and regional hubs.
SHOPS = [
    {
        "name": "Shewa Electronics",
        "slug": "shewa-electronics",
        "city": "Addis Ababa",
        "phone": "+251911100001",
        "description": "Smartphones, laptops, and Apple accessories in Bole Medhanialem.",
        "latitude": Decimal("8.998400"),
        "longitude": Decimal("38.786500"),
        "is_verified": True,
    },
    {
        "name": "Merkato Electronics",
        "slug": "merkato-electronics",
        "city": "Addis Ababa",
        "phone": "+251911100002",
        "description": "Wholesale & retail electronics, phones, and sound gear at Military Tera.",
        "latitude": Decimal("9.030500"),
        "longitude": Decimal("38.740200"),
        "is_verified": True,
    },
    {
        "name": "Bahir Dar Tech Hub",
        "slug": "bahir-dar-tech",
        "city": "Bahir Dar",
        "phone": "+251911100003",
        "description": "Computers, audio gear, and phone accessories in Kebele 04.",
        "latitude": Decimal("11.593600"),
        "longitude": Decimal("37.390800"),
        "is_verified": False,
    },
    {
        "name": "Megenagna Digital",
        "slug": "megenagna-digital",
        "city": "Addis Ababa",
        "phone": "+251911100004",
        "description": "Laptops, tablets, and smart home gadgets next to Zefmesh Grand Mall.",
        "latitude": Decimal("9.019800"),
        "longitude": Decimal("38.799500"),
        "is_verified": True,
    },
    {
        "name": "Piassa Camera & Sound",
        "slug": "piassa-camera-sound",
        "city": "Addis Ababa",
        "phone": "+251911100005",
        "description": "Professional mirrorless cameras, lenses, studio lighting, and audio.",
        "latitude": Decimal("9.035100"),
        "longitude": Decimal("38.752300"),
        "is_verified": True,
    },
    {
        "name": "Hawassa Mobile & PC",
        "slug": "hawassa-mobile-pc",
        "city": "Hawassa",
        "phone": "+251911100006",
        "description": "New & refurbished laptops, phones, and power accessories in Hawassa Piassa.",
        "latitude": Decimal("7.050400"),
        "longitude": Decimal("38.476300"),
        "is_verified": False,
    },
    {
        "name": "Kazanchis Gadget Lab",
        "slug": "kazanchis-gadget-lab",
        "city": "Addis Ababa",
        "phone": "+251911100007",
        "description": "MacBooks, flagship Androids, and premium desktop accessories near UNECA.",
        "latitude": Decimal("9.016200"),
        "longitude": Decimal("38.769000"),
        "is_verified": True,
    },
    {
        "name": "Mexico Tech Corner",
        "slug": "mexico-tech-corner",
        "city": "Addis Ababa",
        "phone": "+251911100008",
        "description": "PC components, gaming laptops, and tablet gear near Mexico Square.",
        "latitude": Decimal("9.010800"),
        "longitude": Decimal("38.746600"),
        "is_verified": True,
    },
]

# High-resolution public royalty-free imagery curated from Unsplash CDN for each category.
UNSPLASH_PUBLIC_IMAGES = {
    "phones": [
        "https://images.unsplash.com/photo-1592750475338-74b7b21085ab?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1511707171634-5f897ff02560?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1610945265064-0e34e5519bbf?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1598327105666-5b89351aff97?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1580910051074-3eb694886505?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1565849904461-04a58ad377e0?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1574944985070-8f3ebc6b79d2?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1585060544812-6b45742d762f?auto=format&fit=crop&w=800&q=80",
    ],
    "laptops": [
        "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1531297484001-80022131f5a1?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1603302576837-37561b2e2302?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1541807084-5c52b6b3adef?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=800&q=80",
    ],
    "audio": [
        "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1546435770-a3e426bf472b?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1484704849700-f032a568e944?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1572536147248-ac59a8abfa4b?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1545454675-3531b543be5d?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1524678606370-a47ad25cb82a?auto=format&fit=crop&w=800&q=80",
    ],
    "tablets": [
        "https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1561154464-82e9adf32764?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1585790050230-5dd28404ccb9?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1512499617640-c74ae3a79d37?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1527698266440-12104e498b76?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1589739900243-4b52cd9b104e?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1568605117036-5fe5e7bab0b7?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=800&q=80",
    ],
    "cameras": [
        "https://images.unsplash.com/photo-1516035069371-29a1b244cc32?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1502920917128-1aa500764cbd?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1526170375885-4d8ecf77b99f?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1512790182412-b19e6d62bc39?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1500634245200-e5245c7574ef?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1617005082133-548c4dd27f35?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1564466809058-bf4114d55352?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1495707902641-75cac588d2e9?auto=format&fit=crop&w=800&q=80",
    ],
    "accessories": [
        "https://images.unsplash.com/photo-1609091839311-d5365f9ff1c5?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1583863788434-e58a36330cf0?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1601784551446-20c9e07cdbdb?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1585338107529-13afc5f02586?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1622445262464-84b1456045b6?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1527864550417-7fd91fc51a46?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1597872200969-2b65d56bd16b?auto=format&fit=crop&w=800&q=80",
    ],
}


# Palettes for the procedural renderer: background, device outline, device fill,
# accent, text, subtext.
PALETTES = [
    ("#F7F6F2", "#D8D4C8", "#FFFFFF", "#157049", "#111111", "#666666"),
    ("#F0F3F5", "#CBD5E1", "#FFFFFF", "#2563EB", "#0F172A", "#64748B"),
    ("#F5F3EF", "#E2DDD5", "#FFFFFF", "#B45309", "#1C1917", "#78716C"),
    ("#1E2229", "#333945", "#262C36", "#38BDF8", "#F8FAFC", "#94A3B8"),
    ("#18181B", "#27272A", "#222226", "#A855F7", "#FAFAFA", "#A1A1AA"),
]

# Rotate the palette per view so the four shots in one gallery are visibly
# different without being four different products.
_VIEW_PALETTE_OFFSET = {"front": 0, "back": 1, "detail": 0, "box": 2}


def _draw_device(draw, category_slug, cx, cy, palette):
    """Draw the category's device silhouette centred on (cx, cy)."""
    bg_hex, dev_border, dev_fill, accent_hex, text_hex, subtext_hex = palette

    if category_slug == "phones":
        pw, ph = 240, 420
        px0, py0 = cx - pw // 2, cy - ph // 2
        px1, py1 = cx + pw // 2, cy + ph // 2
        draw.rounded_rectangle(
            [px0, py0, px1, py1],
            radius=38,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        draw.rounded_rectangle(
            [px0 + 12, py0 + 14, px1 - 12, py1 - 14],
            radius=28,
            fill=bg_hex,
        )
        draw.rounded_rectangle(
            [cx - 36, py0 + 26, cx + 36, py0 + 44], radius=9, fill=dev_border
        )
        draw.line([cx - 20, py1 - 32, cx + 20, py1 - 32], fill=dev_border, width=4)

    elif category_slug == "laptops":
        sw, sh = 420, 260
        sx0, sy0 = cx - sw // 2, cy - sh // 2 - 40
        sx1, sy1 = cx + sw // 2, cy + sh // 2 - 40
        draw.rounded_rectangle(
            [sx0, sy0, sx1, sy1],
            radius=16,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        draw.rectangle([sx0 + 14, sy0 + 14, sx1 - 14, sy1 - 14], fill=bg_hex)
        draw.ellipse([cx - 4, sy0 + 6, cx + 4, sy0 + 14], fill=dev_border)
        bw, bh = 480, 24
        bx0, by0 = cx - bw // 2, sy1
        bx1, by1 = cx + bw // 2, sy1 + bh
        draw.rounded_rectangle(
            [bx0, by0, bx1, by1], radius=8, fill=dev_fill, outline=dev_border, width=3
        )
        draw.rounded_rectangle(
            [cx - 45, sy1, cx + 45, sy1 + 8], radius=3, fill=dev_border
        )

    elif category_slug == "audio":
        hx0, hy0 = cx - 150, cy - 140
        hx1, hy1 = cx + 150, cy + 120
        draw.arc([hx0, hy0, hx1, hy1], start=180, end=0, fill=dev_border, width=16)
        draw.rounded_rectangle(
            [cx - 165, cy - 40, cx - 115, cy + 100],
            radius=24,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        draw.rounded_rectangle(
            [cx + 115, cy - 40, cx + 165, cy + 100],
            radius=24,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        draw.ellipse([cx - 30, cy - 10, cx + 30, cy + 50], outline=accent_hex, width=3)
        draw.ellipse([cx - 15, cy + 5, cx + 15, cy + 35], fill=accent_hex)

    elif category_slug == "tablets":
        tw, th = 360, 440
        tx0, ty0 = cx - tw // 2, cy - th // 2
        tx1, ty1 = cx + tw // 2, cy + th // 2
        draw.rounded_rectangle(
            [tx0, ty0, tx1, ty1],
            radius=28,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        draw.rounded_rectangle(
            [tx0 + 16, ty0 + 16, tx1 - 16, ty1 - 16],
            radius=16,
            fill=bg_hex,
        )
        draw.ellipse([cx - 4, ty0 + 6, cx + 4, ty0 + 14], fill=dev_border)
        px = tx1 + 24
        draw.rounded_rectangle(
            [px, ty0 + 60, px + 12, ty1 - 60],
            radius=6,
            fill=dev_fill,
            outline=dev_border,
            width=2,
        )

    elif category_slug == "cameras":
        cw, ch = 380, 240
        cx0, cy0 = cx - cw // 2, cy - ch // 2
        cx1, cy1 = cx + cw // 2, cy + ch // 2
        draw.rounded_rectangle(
            [cx0, cy0, cx1, cy1],
            radius=24,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        draw.rounded_rectangle(
            [cx0 - 16, cy0 + 30, cx0 + 20, cy1 - 20], radius=12, fill=dev_border
        )
        draw.rectangle([cx - 30, cy0 - 14, cx + 30, cy0], fill=dev_border)
        draw.ellipse(
            [cx - 85, cy - 85, cx + 85, cy + 85],
            fill=dev_border,
            outline=dev_fill,
            width=6,
        )
        draw.ellipse([cx - 65, cy - 65, cx + 65, cy + 65], fill=bg_hex)
        draw.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=accent_hex)
        draw.ellipse([cx - 20, cy - 25, cx - 10, cy - 15], fill="#FFFFFF")

    else:
        aw, ah = 280, 320
        ax0, ay0 = cx - aw // 2, cy - ah // 2
        ax1, ay1 = cx + aw // 2, cy + ah // 2
        draw.rounded_rectangle(
            [ax0, ay0, ax1, ay1],
            radius=28,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        for i in range(4):
            lx = cx - 45 + i * 30
            fill_c = accent_hex if i < 3 else dev_border
            draw.ellipse([lx - 6, cy - 70, lx + 6, cy - 58], fill=fill_c)
        draw.rounded_rectangle(
            [cx - 40, cy + 60, cx + 40, cy + 76], radius=6, fill=dev_border
        )
        draw.rounded_rectangle(
            [cx - 40, cy + 92, cx + 40, cy + 108], radius=6, fill=dev_border
        )


def _draw_rear_cluster(draw, category_slug, cx, cy, palette):
    """Overlay that turns the front silhouette into a back-of-device shot."""
    bg_hex, dev_border, dev_fill, accent_hex, text_hex, subtext_hex = palette
    if category_slug in ("phones", "tablets"):
        bx0, by0 = cx - 88, cy - 185
        draw.rounded_rectangle(
            [bx0, by0, bx0 + 150, by0 + 150],
            radius=32,
            fill=dev_fill,
            outline=dev_border,
            width=4,
        )
        for i in range(3):
            lx = bx0 + 42 + (i % 2) * 66
            ly = by0 + 42 + (i // 2) * 66
            draw.ellipse(
                [lx - 24, ly - 24, lx + 24, ly + 24],
                fill=bg_hex,
                outline=dev_border,
                width=3,
            )
            draw.ellipse([lx - 10, ly - 10, lx + 10, ly + 10], fill=accent_hex)
    else:
        draw.rounded_rectangle(
            [cx - 110, cy - 22, cx + 110, cy + 22],
            radius=12,
            fill=dev_fill,
            outline=dev_border,
            width=3,
        )
        draw.text((cx - 96, cy - 8), "SERIAL / MODEL PLATE", fill=subtext_hex)


def _draw_package(draw, cx, cy, palette):
    """A retail carton, for the fourth shot in the gallery."""
    bg_hex, dev_border, dev_fill, accent_hex, text_hex, subtext_hex = palette
    bw, bh, depth = 340, 260, 70
    x0, y0 = cx - bw // 2, cy - bh // 2 + 20
    x1, y1 = cx + bw // 2, cy + bh // 2 + 20
    draw.polygon(
        [(x0, y0), (x0 + depth, y0 - depth), (x1 + depth, y0 - depth), (x1, y0)],
        fill=dev_fill,
        outline=dev_border,
    )
    draw.polygon(
        [(x1, y0), (x1 + depth, y0 - depth), (x1 + depth, y1 - depth), (x1, y1)],
        fill=dev_border,
    )
    draw.rectangle([x0, y0, x1, y1], fill=dev_fill, outline=dev_border, width=4)
    draw.rounded_rectangle(
        [x0 + 40, y0 + 96, x1 - 40, y0 + 164], radius=10, fill=accent_hex
    )


def _draw_chrome(draw, width, height, category_slug, view, brand, name, spec, palette):
    """Frame, category chip, view chip, and the caption card."""
    bg_hex, dev_border, dev_fill, accent_hex, text_hex, subtext_hex = palette
    draw.rectangle([16, 16, width - 16, height - 16], outline=dev_border, width=2)
    draw.rounded_rectangle([48, 48, 220, 84], radius=18, fill=dev_border)
    draw.text((64, 58), category_slug.upper(), fill=text_hex)
    draw.rounded_rectangle(
        [width - 200, 48, width - 48, 84], radius=18, fill=accent_hex
    )
    draw.text((width - 186, 58), view.upper(), fill=bg_hex)

    card_y = height - 160
    draw.rectangle(
        [36, card_y, width - 36, height - 36],
        fill=dev_fill,
        outline=dev_border,
        width=2,
    )
    draw.text((56, card_y + 18), brand.upper(), fill=accent_hex)
    draw.text((56, card_y + 44), f"{brand} {name}"[:44], fill=text_hex)
    if spec:
        draw.text((56, card_y + 74), spec[:48], fill=subtext_hex)


def generate_procedural_image(
    category_slug: str,
    brand: str,
    name: str,
    spec_summary: str = "",
    variant_idx: int = 0,
    view: str = "front",
) -> bytes:
    """Draw one 800x800 product mockup with Pillow. Offline and deterministic.

    `view` is one of PRODUCT_IMAGE_VIEWS. The silhouette stays the same across
    views on purpose -- it is meant to be the same product -- and what changes is
    the shot: palette, a rear camera cluster, a zoom crop, or packaging.
    """
    width, height = 800, 800
    palette = PALETTES[
        (variant_idx + _VIEW_PALETTE_OFFSET.get(view, 0)) % len(PALETTES)
    ]
    bg_hex = palette[0]

    image = Image.new("RGB", (width, height), color=bg_hex)
    draw = ImageDraw.Draw(image)
    cx, cy = width // 2, height // 2 - 20

    if view == "box":
        _draw_package(draw, cx, cy, palette)
    else:
        _draw_device(draw, category_slug, cx, cy, palette)
        if view == "back":
            _draw_rear_cluster(draw, category_slug, cx, cy, palette)

    if view == "detail":
        # A real close-up: zoom the device and let the crop fall where it may.
        # The chrome is drawn afterwards so the caption survives the resize.
        half = int(width / (2 * 1.9))
        image = image.crop((cx - half, cy - half, cx + half, cy + half)).resize(
            (width, height), Image.LANCZOS
        )
        draw = ImageDraw.Draw(image)

    _draw_chrome(
        draw, width, height, category_slug, view, brand, name, spec_summary, palette
    )

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


def shrink_to_budget(data: bytes, budget: int = SEED_IMAGE_MAX_BYTES):
    """Return `data` re-encoded under `budget`, or None if it will not fit.

    The ceiling has to be enforced here or not at all: these bytes go straight
    into MEDIA_ROOT, `validate_image_file` tops out at 5 MB, and the seeder
    bypasses even that by constructing ProductImage directly instead of going
    through an upload.
    """
    if len(data) <= budget:
        return data
    try:
        with Image.open(io.BytesIO(data)) as opened:
            rgb = opened.convert("RGB")
    except Exception:
        return None
    for edge, quality in ((800, 80), (800, 65), (600, 65), (600, 50)):
        scaled = rgb.copy()
        scaled.thumbnail((edge, edge))
        buf = io.BytesIO()
        scaled.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= budget:
            return buf.getvalue()
    return None


def get_category_image_bytes(
    category_slug: str,
    brand: str,
    name: str,
    spec_summary: str,
    variant_idx: int,
    source_mode: str = "auto",
    view: str = "front",
) -> bytes:
    """Bytes for one product view, guaranteed under SEED_IMAGE_MAX_BYTES.

    Only the front view is ever fetched. The stock pool holds 8 photos per
    category and they are 8 different devices, so drawing the other three views
    from it would put four unrelated products in one gallery -- which reads as
    broken data far more loudly than a single image ever did.

    Only downloads are cached. Procedural output is keyed on the product's own
    brand, name and specs, so caching it per category would stamp one product's
    caption onto every other product that shared the slot.
    """
    urls = UNSPLASH_PUBLIC_IMAGES.get(category_slug, [])
    url_idx = variant_idx % len(urls) if urls else 0
    networked = view == "front" and source_mode in ("auto", "unsplash") and bool(urls)

    if networked:
        cache_dir = settings.MEDIA_ROOT / "seed_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{category_slug}_{url_idx}.jpg"

        if cache_file.exists():
            try:
                cached = cache_file.read_bytes()
                if len(cached) <= SEED_IMAGE_MAX_BYTES:
                    return cached
            except OSError:
                pass

        try:
            req = urllib.request.Request(
                urls[url_idx], headers={"User-Agent": "Tinsae-Seeder/1.0"}
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = resp.read()
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            data = shrink_to_budget(data)
            if data is not None:
                cache_file.write_bytes(data)
                return data
        except Exception:
            # Network failure, timeout, an unreadable body, or a file that will
            # not fit the budget: fall through rather than fail the whole seed.
            pass

    return generate_procedural_image(
        category_slug=category_slug,
        brand=brand,
        name=name,
        spec_summary=spec_summary,
        variant_idx=variant_idx,
        view=view,
    )


# Model Catalog Matrices for Procedural Generation
CATALOG_MATRICES = {
    "phones": [
        {
            "brand": "Apple",
            "name": "iPhone 15 Pro Max",
            "base_price": 142000,
            "colors": [
                "Natural Titanium",
                "Black Titanium",
                "White Titanium",
                "Blue Titanium",
            ],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "8GB"},
                {"storage": "512GB", "price_adj": 16000, "ram": "8GB"},
                {"storage": "1TB", "price_adj": 28000, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.7″ Super Retina XDR OLED 120Hz",
                "chip": "A17 Pro (3nm)",
                "camera": "48MP Main + 12MP 5x Telephoto + 12MP Ultra-wide",
                "battery": "4,422 mAh 29W",
                "n5g": "Yes",
                "year": "2023",
                "weight": "221 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Apple",
            "name": "iPhone 15 Pro",
            "base_price": 118000,
            "colors": ["Natural Titanium", "Black Titanium", "White Titanium"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "8GB"},
                {"storage": "256GB", "price_adj": 12000, "ram": "8GB"},
                {"storage": "512GB", "price_adj": 24000, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.1″ Super Retina XDR OLED 120Hz",
                "chip": "A17 Pro (3nm)",
                "camera": "48MP Main + 12MP 3x Telephoto + 12MP Ultra-wide",
                "battery": "3,274 mAh 27W",
                "n5g": "Yes",
                "year": "2023",
                "weight": "187 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Apple",
            "name": "iPhone 15",
            "base_price": 84000,
            "colors": ["Black", "Blue", "Green", "Yellow", "Pink"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "6GB"},
                {"storage": "256GB", "price_adj": 9000, "ram": "6GB"},
            ],
            "specs": {
                "display": "6.1″ Super Retina XDR OLED Dynamic Island",
                "chip": "A16 Bionic",
                "camera": "48MP Main + 12MP Ultra-wide",
                "battery": "3,349 mAh 20W",
                "n5g": "Yes",
                "year": "2023",
                "weight": "171 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Apple",
            "name": "iPhone 14 Pro Max",
            "base_price": 108000,
            "colors": ["Deep Purple", "Space Black", "Gold", "Silver"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "6GB"},
                {"storage": "256GB", "price_adj": 11000, "ram": "6GB"},
            ],
            "specs": {
                "display": "6.7″ OLED 120Hz Dynamic Island",
                "chip": "A16 Bionic",
                "camera": "48MP Main + 12MP 3x Telephoto",
                "battery": "4,323 mAh",
                "n5g": "Yes",
                "year": "2022",
                "weight": "240 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Apple",
            "name": "iPhone 13",
            "base_price": 54000,
            "colors": ["Midnight", "Starlight", "Blue", "Pink", "Green"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "4GB"},
                {"storage": "256GB", "price_adj": 7000, "ram": "4GB"},
            ],
            "specs": {
                "display": "6.1″ Super Retina XDR OLED",
                "chip": "A15 Bionic",
                "camera": "12MP Dual Wide/Ultra-wide OIS",
                "battery": "3,240 mAh",
                "n5g": "Yes",
                "year": "2021",
                "weight": "174 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy S24 Ultra",
            "base_price": 148000,
            "colors": [
                "Titanium Gray",
                "Titanium Black",
                "Titanium Violet",
                "Titanium Yellow",
            ],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "12GB"},
                {"storage": "512GB", "price_adj": 18000, "ram": "12GB"},
            ],
            "specs": {
                "display": "6.8″ Dynamic AMOLED 2X 120Hz Gorilla Armor",
                "chip": "Snapdragon 8 Gen 3 for Galaxy",
                "camera": "200MP Quad OIS + 50MP 5x Periscope",
                "battery": "5,000 mAh 45W",
                "n5g": "Yes",
                "year": "2024",
                "weight": "232 g",
                "sim": "Dual Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy S24",
            "base_price": 79000,
            "colors": ["Onyx Black", "Marble Gray", "Cobalt Violet", "Amber Yellow"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "8GB"},
                {"storage": "256GB", "price_adj": 8000, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.2″ Dynamic AMOLED 2X 120Hz",
                "chip": "Exynos 2400 Deca-Core",
                "camera": "50MP Triple OIS",
                "battery": "4,000 mAh 25W",
                "n5g": "Yes",
                "year": "2024",
                "weight": "167 g",
                "sim": "Dual Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy Z Flip 5",
            "base_price": 91000,
            "colors": ["Mint", "Graphite", "Cream", "Lavender"],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "8GB"},
                {"storage": "512GB", "price_adj": 11000, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.7″ Foldable AMOLED 120Hz + 3.4″ Cover",
                "chip": "Snapdragon 8 Gen 2",
                "camera": "12MP Dual OIS",
                "battery": "3,700 mAh",
                "n5g": "Yes",
                "year": "2023",
                "weight": "187 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy A55 5G",
            "base_price": 39500,
            "colors": [
                "Awesome Navy",
                "Awesome Iceblue",
                "Awesome Lilac",
                "Awesome Lemon",
            ],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "8GB"},
                {"storage": "256GB", "price_adj": 4500, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.6″ Super AMOLED 120Hz HDR10+",
                "chip": "Exynos 1480 (4nm)",
                "camera": "50MP Triple OIS + 12MP Ultra-wide",
                "battery": "5,000 mAh 25W",
                "n5g": "Yes",
                "year": "2024",
                "weight": "213 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy A15",
            "base_price": 12800,
            "colors": ["Blue Black", "Light Blue", "Yellow"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "4GB"},
                {"storage": "128GB", "price_adj": 1500, "ram": "6GB"},
            ],
            "specs": {
                "display": "6.5″ Super AMOLED 90Hz",
                "chip": "Helio G99",
                "camera": "50MP Triple",
                "battery": "5,000 mAh 25W",
                "n5g": "No",
                "year": "2023",
                "weight": "200 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Xiaomi",
            "name": "Redmi Note 13 Pro+ 5G",
            "base_price": 41000,
            "colors": ["Midnight Black", "Moonlight White", "Aurora Purple"],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "8GB"},
                {"storage": "512GB", "price_adj": 6000, "ram": "12GB"},
            ],
            "specs": {
                "display": "6.67″ Curved CrystalRes AMOLED 120Hz 1800 nits",
                "chip": "Dimensity 7200 Ultra",
                "camera": "200MP Triple OIS",
                "battery": "5,000 mAh 120W HyperCharge",
                "n5g": "Yes",
                "year": "2024",
                "weight": "204 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Xiaomi",
            "name": "Redmi Note 13",
            "base_price": 14500,
            "colors": ["Midnight Black", "Mint Green", "Ice Blue"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "6GB"},
                {"storage": "256GB", "price_adj": 2200, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.67″ AMOLED 120Hz",
                "chip": "Snapdragon 685",
                "camera": "108MP Triple",
                "battery": "5,000 mAh 33W",
                "n5g": "No",
                "year": "2024",
                "weight": "188 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Xiaomi",
            "name": "POCO X6 Pro",
            "base_price": 33500,
            "colors": ["POCO Yellow", "Black", "Grey"],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "8GB"},
                {"storage": "512GB", "price_adj": 4500, "ram": "12GB"},
            ],
            "specs": {
                "display": "6.67″ AMOLED 120Hz Dolby Vision",
                "chip": "Dimensity 8300 Ultra",
                "camera": "64MP Triple OIS",
                "battery": "5,000 mAh 67W Turbo",
                "n5g": "Yes",
                "year": "2024",
                "weight": "186 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Tecno",
            "name": "Camon 30 Pro 5G",
            "base_price": 25500,
            "colors": ["Iceland Basaltic Dark", "Alps Snowy Silver"],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "12GB"},
                {"storage": "512GB", "price_adj": 3500, "ram": "12GB"},
            ],
            "specs": {
                "display": "6.78″ AMOLED 144Hz",
                "chip": "Dimensity 8200 Ultimate",
                "camera": "50MP Triple Sony IMX890 OIS",
                "battery": "5,000 mAh 70W",
                "n5g": "Yes",
                "year": "2024",
                "weight": "188 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Tecno",
            "name": "Spark 20 Pro+",
            "base_price": 14200,
            "colors": ["Temporal Orbits", "Lunar Frost", "Radiant Starstream"],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.78″ Curved AMOLED 120Hz",
                "chip": "Helio G99 Ultimate",
                "camera": "108MP Dual",
                "battery": "5,000 mAh 33W",
                "n5g": "No",
                "year": "2024",
                "weight": "179 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Infinix",
            "name": "Note 40 Pro",
            "base_price": 17500,
            "colors": ["Vintage Green", "Titan Gold"],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.78″ 3D-Curved AMOLED 120Hz",
                "chip": "Helio G99 Ultimate",
                "camera": "108MP Triple OIS 3x Lossless Zoom",
                "battery": "5,000 mAh 70W All-Round FastCharge",
                "n5g": "No",
                "year": "2024",
                "weight": "190 g",
                "sim": "Dual Nano-SIM",
            },
        },
        {
            "brand": "Google",
            "name": "Pixel 8 Pro",
            "base_price": 92000,
            "colors": ["Obsidian", "Porcelain", "Bay Blue"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "12GB"},
                {"storage": "256GB", "price_adj": 9000, "ram": "12GB"},
            ],
            "specs": {
                "display": "6.7″ Super Actua LTPO OLED 120Hz 2400 nits",
                "chip": "Google Tensor G3 + Titan M2",
                "camera": "50MP Main + 48MP 5x Telephoto + 48MP Ultra-wide",
                "battery": "5,050 mAh 30W",
                "n5g": "Yes",
                "year": "2023",
                "weight": "213 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "Google",
            "name": "Pixel 7a",
            "base_price": 43500,
            "colors": ["Charcoal", "Snow", "Sea", "Coral"],
            "variants": [
                {"storage": "128GB", "price_adj": 0, "ram": "8GB"},
            ],
            "specs": {
                "display": "6.1″ OLED 90Hz Smooth Display",
                "chip": "Google Tensor G2",
                "camera": "64MP Quad PD Quad Bayer Main + 13MP Ultra-wide",
                "battery": "4,385 mAh",
                "n5g": "Yes",
                "year": "2023",
                "weight": "193 g",
                "sim": "Nano-SIM + eSIM",
            },
        },
        {
            "brand": "OnePlus",
            "name": "OnePlus 12",
            "base_price": 102000,
            "colors": ["Silky Black", "Flowy Emerald"],
            "variants": [
                {"storage": "256GB", "price_adj": 0, "ram": "12GB"},
                {"storage": "512GB", "price_adj": 12000, "ram": "16GB"},
            ],
            "specs": {
                "display": "6.82″ ProXDR LTPO AMOLED 120Hz 4500 nits",
                "chip": "Snapdragon 8 Gen 3",
                "camera": "50MP Sony LYT-808 + 64MP 3x Periscope Hasselblad",
                "battery": "5,400 mAh 100W SUPERVOOC",
                "n5g": "Yes",
                "year": "2024",
                "weight": "220 g",
                "sim": "Dual Nano-SIM",
            },
        },
    ],
    "laptops": [
        {
            "brand": "Apple",
            "name": "MacBook Pro 16″",
            "base_price": 248000,
            "colors": ["Space Black", "Silver"],
            "variants": [
                {
                    "processor": "M3 Pro 12-core",
                    "ram": "18GB",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "M3 Max 14-core",
                    "ram": "36GB",
                    "storage": "1TB SSD",
                    "price_adj": 38000,
                },
            ],
            "specs": {
                "display": "16.2″ Liquid Retina XDR 120Hz ProMotion 1600 nits",
                "gpu": "18-core / 30-core GPU",
                "battery": "100Wh up to 22h",
                "weight": "2.14 kg",
                "os": "macOS Sonoma",
            },
        },
        {
            "brand": "Apple",
            "name": "MacBook Pro 14″",
            "base_price": 165000,
            "colors": ["Space Black", "Silver"],
            "variants": [
                {
                    "processor": "M3 Pro 11-core",
                    "ram": "18GB",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "M3 Max 14-core",
                    "ram": "36GB",
                    "storage": "1TB SSD",
                    "price_adj": 42000,
                },
            ],
            "specs": {
                "display": "14.2″ Liquid Retina XDR 120Hz ProMotion",
                "gpu": "14-core GPU",
                "battery": "70Wh up to 18h",
                "weight": "1.61 kg",
                "os": "macOS Sonoma",
            },
        },
        {
            "brand": "Apple",
            "name": "MacBook Air 15″",
            "base_price": 128000,
            "colors": ["Midnight", "Starlight", "Space Gray", "Silver"],
            "variants": [
                {
                    "processor": "Apple M3 8-core",
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
            ],
            "specs": {
                "display": "15.3″ Liquid Retina 500 nits True Tone",
                "gpu": "10-core GPU",
                "battery": "66.5Wh up to 18h",
                "weight": "1.51 kg",
                "os": "macOS Sonoma",
            },
        },
        {
            "brand": "Apple",
            "name": "MacBook Air 13″",
            "base_price": 98000,
            "colors": ["Midnight", "Starlight", "Space Gray", "Silver"],
            "variants": [
                {
                    "processor": "Apple M3 8-core",
                    "ram": "8GB",
                    "storage": "256GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "Apple M3 8-core",
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "price_adj": 16000,
                },
            ],
            "specs": {
                "display": "13.6″ Liquid Retina True Tone",
                "gpu": "8-core / 10-core GPU",
                "battery": "52.6Wh up to 18h",
                "weight": "1.24 kg",
                "os": "macOS Sonoma",
            },
        },
        {
            "brand": "Dell",
            "name": "XPS 15 9530",
            "base_price": 148000,
            "colors": ["Platinum Silver with Black Carbon Fiber"],
            "variants": [
                {
                    "processor": "Intel Core i7-13700H",
                    "ram": "16GB DDR5",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "Intel Core i9-13900H",
                    "ram": "32GB DDR5",
                    "storage": "1TB SSD",
                    "price_adj": 26000,
                },
            ],
            "specs": {
                "display": "15.6″ 3.5K OLED Touch 400 nits InfinityEdge",
                "gpu": "NVIDIA GeForce RTX 4060 8GB GDDR6",
                "battery": "86Wh ExpressCharge",
                "weight": "1.92 kg",
                "os": "Windows 11 Pro",
            },
        },
        {
            "brand": "Dell",
            "name": "Inspiron 15 3520",
            "base_price": 38000,
            "colors": ["Carbon Black", "Platinum Silver"],
            "variants": [
                {
                    "processor": "Intel Core i5-1235U",
                    "ram": "8GB DDR4",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "Intel Core i7-1255U",
                    "ram": "16GB DDR4",
                    "storage": "512GB SSD",
                    "price_adj": 6500,
                },
            ],
            "specs": {
                "display": "15.6″ FHD 120Hz Anti-glare",
                "gpu": "Intel Iris Xe Graphics",
                "battery": "41Wh",
                "weight": "1.65 kg",
                "os": "Windows 11 Home",
            },
        },
        {
            "brand": "Lenovo",
            "name": "ThinkPad X1 Carbon Gen 11",
            "base_price": 142000,
            "colors": ["Deep Black"],
            "variants": [
                {
                    "processor": "Intel Core i7-1365U vPro",
                    "ram": "16GB LPDDR5",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "Intel Core i7-1370P",
                    "ram": "32GB LPDDR5",
                    "storage": "1TB SSD",
                    "price_adj": 22000,
                },
            ],
            "specs": {
                "display": "14″ 2.8K OLED Anti-reflection 400 nits",
                "gpu": "Intel Iris Xe Graphics",
                "battery": "57Wh Rapid Charge",
                "weight": "1.12 kg (Ultralight Carbon)",
                "os": "Windows 11 Pro",
            },
        },
        {
            "brand": "Lenovo",
            "name": "ThinkPad T14 Gen 4",
            "base_price": 76000,
            "colors": ["Thunder Black"],
            "variants": [
                {
                    "processor": "AMD Ryzen 7 PRO 7840U",
                    "ram": "16GB LPDDR5x",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "Intel Core i7-1355U",
                    "ram": "32GB DDR5",
                    "storage": "1TB SSD",
                    "price_adj": 12000,
                },
            ],
            "specs": {
                "display": "14″ WUXGA IPS 400 nits Low Power",
                "gpu": "AMD Radeon 780M / Intel Iris Xe",
                "battery": "52.5Wh",
                "weight": "1.34 kg",
                "os": "Windows 11 Pro",
            },
        },
        {
            "brand": "Lenovo",
            "name": "Legion Pro 5 Gen 8",
            "base_price": 135000,
            "colors": ["Onyx Grey"],
            "variants": [
                {
                    "processor": "AMD Ryzen 7 7745HX",
                    "ram": "16GB DDR5",
                    "storage": "1TB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "Intel Core i7-13700HX",
                    "ram": "32GB DDR5",
                    "storage": "1TB SSD",
                    "price_adj": 15000,
                },
            ],
            "specs": {
                "display": "16″ WQXGA 240Hz 500 nits G-SYNC",
                "gpu": "NVIDIA GeForce RTX 4070 8GB (140W TGP)",
                "battery": "80Wh Super Rapid Charge",
                "weight": "2.55 kg",
                "os": "Windows 11 Home",
            },
        },
        {
            "brand": "HP",
            "name": "Spectre x360 14 2-in-1",
            "base_price": 132000,
            "colors": ["Nightfall Black", "Natural Silver"],
            "variants": [
                {
                    "processor": "Intel Core Ultra 7 155H",
                    "ram": "16GB LPDDR5x",
                    "storage": "1TB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "Intel Core Ultra 7 155H",
                    "ram": "32GB LPDDR5x",
                    "storage": "2TB SSD",
                    "price_adj": 18000,
                },
            ],
            "specs": {
                "display": "14″ 2.8K OLED Touch 120Hz IMAX Enhanced",
                "gpu": "Intel Arc Graphics",
                "battery": "68Wh Fast Charge",
                "weight": "1.44 kg",
                "os": "Windows 11 Home",
            },
        },
        {
            "brand": "HP",
            "name": "Pavilion 15",
            "base_price": 36000,
            "colors": ["Natural Silver", "Fog Blue"],
            "variants": [
                {
                    "processor": "Intel Core i5-1235U",
                    "ram": "8GB DDR4",
                    "storage": "512GB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "AMD Ryzen 5 7530U",
                    "ram": "16GB DDR4",
                    "storage": "512GB SSD",
                    "price_adj": 5000,
                },
            ],
            "specs": {
                "display": "15.6″ FHD IPS micro-edge",
                "gpu": "Intel Iris Xe / AMD Radeon",
                "battery": "41Wh",
                "weight": "1.75 kg",
                "os": "Windows 11 Home",
            },
        },
        {
            "brand": "ASUS",
            "name": "ZenBook 14 OLED",
            "base_price": 99000,
            "colors": ["Ponder Blue", "Foggy Silver"],
            "variants": [
                {
                    "processor": "Intel Core Ultra 7 155H",
                    "ram": "16GB LPDDR5x",
                    "storage": "1TB SSD",
                    "price_adj": 0,
                },
            ],
            "specs": {
                "display": "14″ 3K ASUS Lumina OLED 120Hz 500 nits",
                "gpu": "Intel Arc Graphics",
                "battery": "75Wh up to 15h",
                "weight": "1.28 kg",
                "os": "Windows 11 Home",
            },
        },
        {
            "brand": "ASUS",
            "name": "ROG Zephyrus G14",
            "base_price": 172000,
            "colors": ["Eclipse Gray", "Platinum White"],
            "variants": [
                {
                    "processor": "AMD Ryzen 9 8945HS",
                    "ram": "16GB LPDDR5x",
                    "storage": "1TB SSD",
                    "price_adj": 0,
                },
                {
                    "processor": "AMD Ryzen 9 8945HS",
                    "ram": "32GB LPDDR5x",
                    "storage": "1TB SSD",
                    "price_adj": 20000,
                },
            ],
            "specs": {
                "display": "14″ 3K OLED 120Hz ROG Nebula Display",
                "gpu": "NVIDIA GeForce RTX 4070 8GB GDDR6",
                "battery": "73Wh 100W Type-C",
                "weight": "1.50 kg CNC Aluminum",
                "os": "Windows 11 Home",
            },
        },
    ],
    "audio": [
        {
            "brand": "Sony",
            "name": "WH-1000XM5",
            "base_price": 36000,
            "colors": ["Black", "Silver", "Midnight Blue"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Over-ear Wireless",
                "anc": "Industry-leading Dual Processor V1/QN1 ANC",
                "battery": "30h with ANC (40h without)",
                "bluetooth": "5.2 LDAC Hi-Res Wireless",
                "driver": "30mm Carbon Fiber Composite",
                "water_resistance": "None",
                "weight": "250 g",
            },
        },
        {
            "brand": "Sony",
            "name": "WF-1000XM5",
            "base_price": 23500,
            "colors": ["Black", "Silver"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "True Wireless In-ear",
                "anc": "Dual Feedback Mics + Dynamic Driver X",
                "battery": "8h playback (24h with case)",
                "bluetooth": "5.3 LDAC + LC3",
                "driver": "Dynamic Driver X 8.4mm",
                "water_resistance": "IPX4 Sweat Resistant",
                "weight": "5.9 g per earbud",
            },
        },
        {
            "brand": "Sony",
            "name": "WH-CH720N",
            "base_price": 9500,
            "colors": ["Black", "White", "Blue"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Over-ear Wireless",
                "anc": "Dual Noise Sensor Technology",
                "battery": "35h with ANC (50h without)",
                "bluetooth": "5.2 Multipoint",
                "driver": "30mm Dynamic",
                "water_resistance": "None",
                "weight": "192 g Lightweight",
            },
        },
        {
            "brand": "Apple",
            "name": "AirPods Pro (2nd Gen USB-C)",
            "base_price": 25500,
            "colors": ["White"],
            "variants": [{"edition": "MagSafe Case (USB-C)", "price_adj": 0}],
            "specs": {
                "type": "True Wireless In-ear",
                "anc": "2x More Active Noise Cancellation + Adaptive Audio",
                "battery": "6h listening (30h total with case)",
                "bluetooth": "5.3 Apple H2 Headphone Chip",
                "driver": "Custom high-excursion Apple driver",
                "water_resistance": "IP54 Dust, Sweat, & Water Resistant",
                "weight": "5.3 g per earbud",
            },
        },
        {
            "brand": "Apple",
            "name": "AirPods Max",
            "base_price": 62000,
            "colors": ["Space Gray", "Silver", "Sky Blue", "Pink", "Green"],
            "variants": [{"edition": "Smart Case Included", "price_adj": 0}],
            "specs": {
                "type": "Over-ear Wireless",
                "anc": "Pro-level Active Noise Cancellation + Spatial Audio",
                "battery": "20h listening with ANC",
                "bluetooth": "5.0 Apple H1 Chip (each ear cup)",
                "driver": "40mm Apple-designed dynamic driver",
                "water_resistance": "None",
                "weight": "384.8 g Stainless Steel & Knit Mesh",
            },
        },
        {
            "brand": "Bose",
            "name": "QuietComfort Ultra",
            "base_price": 39500,
            "colors": ["Black", "White Smoke", "Sandstone"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Over-ear Wireless",
                "anc": "World-Class CustomTune ANC + Bose Immersive Audio",
                "battery": "24h (18h with Immersive Audio)",
                "bluetooth": "5.3 Snapdragon Sound",
                "driver": "Proprietary Bose Acoustic Architecture",
                "water_resistance": "None",
                "weight": "252 g",
            },
        },
        {
            "brand": "JBL",
            "name": "Charge 5",
            "base_price": 15500,
            "colors": ["Black", "Blue", "Squad Camo", "Red", "Teal"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Rugged Bluetooth Speaker & Powerbank",
                "anc": "None",
                "battery": "20h playtime (built-in 7,500mAh powerbank)",
                "bluetooth": "5.1 PartyBoost Multi-speaker",
                "driver": "52x90mm racetrack driver + separate 20mm tweeter",
                "water_resistance": "IP67 Waterproof & Dustproof",
                "weight": "960 g",
            },
        },
        {
            "brand": "JBL",
            "name": "Tune 770NC",
            "base_price": 7200,
            "colors": ["Black", "Blue", "White"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Over-ear Wireless",
                "anc": "Adaptive Noise Cancelling with Smart Ambient",
                "battery": "Up to 70h (44h with ANC)",
                "bluetooth": "5.3 Pure Bass Sound",
                "driver": "40mm Dynamic Driver",
                "water_resistance": "None",
                "weight": "232 g",
            },
        },
        {
            "brand": "Marshall",
            "name": "Major IV",
            "base_price": 13500,
            "colors": ["Black", "Brown"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "On-ear Wireless",
                "anc": "None",
                "battery": "80+ hours wireless playback + Wireless Charging",
                "bluetooth": "5.0 Multi-directional control knob",
                "driver": "40mm Custom-tuned Dynamic",
                "water_resistance": "None",
                "weight": "165 g Foldable Ergonomic",
            },
        },
        {
            "brand": "Marshall",
            "name": "Emberton II",
            "base_price": 17800,
            "colors": ["Black and Brass", "Cream"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Portable Stereo Speaker",
                "anc": "None",
                "battery": "30+ hours portable playtime",
                "bluetooth": "5.1 True Stereophonic 360° Sound",
                "driver": "Dual 2″ 10W full range drivers",
                "water_resistance": "IP67 Rugged Roadworthy",
                "weight": "700 g",
            },
        },
        {
            "brand": "Sennheiser",
            "name": "Momentum 4 Wireless",
            "base_price": 31000,
            "colors": ["Black", "White", "Denim"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Over-ear Wireless",
                "anc": "Adaptive Noise Cancellation + Transparency Mode",
                "battery": "Incredible 60h battery life",
                "bluetooth": "5.2 aptX Adaptive",
                "driver": "42mm audiophile-grade transducer",
                "water_resistance": "None",
                "weight": "293 g",
            },
        },
        {
            "brand": "Oraimo",
            "name": "FreePods 4",
            "base_price": 2600,
            "colors": ["Black", "White", "Sky Blue"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "True Wireless In-ear",
                "anc": "Active Noise Cancellation up to 30dB",
                "battery": "8.5h playback (35.5h with case)",
                "bluetooth": "5.2 Low Latency Game Mode",
                "driver": "10mm Bass Hybrid Dynamic",
                "water_resistance": "IPX5 Waterproof",
                "weight": "4.8 g per earbud",
            },
        },
        {
            "brand": "Anker",
            "name": "Soundcore Space Q45",
            "base_price": 10500,
            "colors": ["Black", "White", "Navy Blue"],
            "variants": [{"edition": "Standard", "price_adj": 0}],
            "specs": {
                "type": "Over-ear Wireless",
                "anc": "Adaptive Noise Cancelling reduces noise by up to 98%",
                "battery": "50h with ANC (65h in standard)",
                "bluetooth": "5.3 LDAC Hi-Res Wireless Certified",
                "driver": "40mm double-layer diaphragm drivers",
                "water_resistance": "None",
                "weight": "295 g",
            },
        },
    ],
    "tablets": [
        {
            "brand": "Apple",
            "name": "iPad Pro 13″ (M4)",
            "base_price": 155000,
            "colors": ["Space Black", "Silver"],
            "variants": [
                {
                    "storage": "256GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 0,
                    "ram": "8GB",
                },
                {
                    "storage": "512GB",
                    "connectivity": "Wi-Fi + Cellular 5G",
                    "price_adj": 32000,
                    "ram": "8GB",
                },
            ],
            "specs": {
                "display": "13″ Ultra Retina XDR Tandem OLED 120Hz 1600 nits",
                "chip": "Apple M4 (9-core CPU, 10-core GPU)",
                "pen_support": "Apple Pencil Pro & Apple Pencil (USB-C)",
                "battery": "38.99Wh up to 10h",
                "weight": "579 g (5.1mm ultra-thin)",
            },
        },
        {
            "brand": "Apple",
            "name": "iPad Pro 11″ (M4)",
            "base_price": 122000,
            "colors": ["Space Black", "Silver"],
            "variants": [
                {
                    "storage": "256GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 0,
                    "ram": "8GB",
                },
                {
                    "storage": "512GB",
                    "connectivity": "Wi-Fi + Cellular 5G",
                    "price_adj": 28000,
                    "ram": "8GB",
                },
            ],
            "specs": {
                "display": "11″ Ultra Retina XDR Tandem OLED 120Hz ProMotion",
                "chip": "Apple M4 (9-core CPU, 10-core GPU)",
                "pen_support": "Apple Pencil Pro",
                "battery": "31.29Wh up to 10h",
                "weight": "444 g (5.3mm)",
            },
        },
        {
            "brand": "Apple",
            "name": "iPad Air 11″ (M2)",
            "base_price": 74000,
            "colors": ["Space Gray", "Starlight", "Blue", "Purple"],
            "variants": [
                {
                    "storage": "128GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 0,
                    "ram": "8GB",
                },
                {
                    "storage": "256GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 12000,
                    "ram": "8GB",
                },
            ],
            "specs": {
                "display": "11″ Liquid Retina 500 nits P3 True Tone",
                "chip": "Apple M2 (8-core CPU, 10-core GPU)",
                "pen_support": "Apple Pencil Pro & Apple Pencil (USB-C)",
                "battery": "28.93Wh up to 10h",
                "weight": "462 g",
            },
        },
        {
            "brand": "Apple",
            "name": "iPad 10th Gen (10.9″)",
            "base_price": 46000,
            "colors": ["Silver", "Blue", "Pink", "Yellow"],
            "variants": [
                {
                    "storage": "64GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 0,
                    "ram": "4GB",
                },
                {
                    "storage": "256GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 12000,
                    "ram": "4GB",
                },
            ],
            "specs": {
                "display": "10.9″ Liquid Retina True Tone",
                "chip": "A14 Bionic",
                "pen_support": "Apple Pencil (1st Gen & USB-C)",
                "battery": "28.6Wh up to 10h",
                "weight": "477 g",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy Tab S9 Ultra",
            "base_price": 138000,
            "colors": ["Graphite", "Beige"],
            "variants": [
                {
                    "storage": "256GB",
                    "connectivity": "Wi-Fi + 5G",
                    "price_adj": 0,
                    "ram": "12GB",
                },
                {
                    "storage": "512GB",
                    "connectivity": "Wi-Fi + 5G",
                    "price_adj": 18000,
                    "ram": "12GB",
                },
            ],
            "specs": {
                "display": "14.6″ Dynamic AMOLED 2X 120Hz HDR10+",
                "chip": "Snapdragon 8 Gen 2 for Galaxy",
                "pen_support": "S-Pen with Bluetooth Gestures Included",
                "battery": "11,200 mAh 45W",
                "weight": "732 g IP68 Water Resistant",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy Tab S9 FE",
            "base_price": 44000,
            "colors": ["Gray", "Silver", "Mint", "Lavender"],
            "variants": [
                {
                    "storage": "128GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 0,
                    "ram": "6GB",
                },
                {
                    "storage": "256GB",
                    "connectivity": "Wi-Fi + 5G",
                    "price_adj": 9500,
                    "ram": "8GB",
                },
            ],
            "specs": {
                "display": "10.9″ WQXGA 90Hz IPS Display",
                "chip": "Exynos 1380 (5nm)",
                "pen_support": "IP68 S-Pen Included in Box",
                "battery": "8,000 mAh 45W",
                "weight": "523 g IP68 Water Resistant",
            },
        },
        {
            "brand": "Samsung",
            "name": "Galaxy Tab A9+",
            "base_price": 23500,
            "colors": ["Graphite", "Silver", "Navy"],
            "variants": [
                {
                    "storage": "64GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 0,
                    "ram": "4GB",
                },
                {
                    "storage": "128GB",
                    "connectivity": "5G Cellular",
                    "price_adj": 4500,
                    "ram": "8GB",
                },
            ],
            "specs": {
                "display": "11.0″ FHD 90Hz Quad Speakers",
                "chip": "Snapdragon 695 5G",
                "pen_support": "No",
                "battery": "7,040 mAh 15W",
                "weight": "480 g",
            },
        },
        {
            "brand": "Xiaomi",
            "name": "Pad 6",
            "base_price": 31000,
            "colors": ["Gravity Gray", "Gold", "Mist Blue"],
            "variants": [
                {
                    "storage": "128GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 0,
                    "ram": "6GB",
                },
                {
                    "storage": "256GB",
                    "connectivity": "Wi-Fi",
                    "price_adj": 3500,
                    "ram": "8GB",
                },
            ],
            "specs": {
                "display": "11.0″ 2.8K 144Hz 7-stage Variable Refresh IPS",
                "chip": "Snapdragon 870",
                "pen_support": "Xiaomi Smart Pen 2nd Gen Compatible",
                "battery": "8,840 mAh 33W Fast Charge",
                "weight": "490 g All-metal Unibody",
            },
        },
        {
            "brand": "Microsoft",
            "name": "Surface Pro 9",
            "base_price": 95000,
            "colors": ["Platinum", "Graphite", "Sapphire", "Forest"],
            "variants": [
                {
                    "storage": "256GB SSD",
                    "connectivity": "Wi-Fi 6E",
                    "price_adj": 0,
                    "ram": "8GB",
                },
                {
                    "storage": "512GB SSD",
                    "connectivity": "Wi-Fi 6E",
                    "price_adj": 16000,
                    "ram": "16GB",
                },
            ],
            "specs": {
                "display": "13.0″ PixelSense Flow 120Hz 2880x1920 Touch",
                "chip": "12th Gen Intel Core i5-1235U / i7-1255U",
                "pen_support": "Surface Slim Pen 2 with Haptic Signals",
                "battery": "47.7Wh up to 15.5h",
                "weight": "879 g Anodized Aluminum",
            },
        },
    ],
    "cameras": [
        {
            "brand": "Sony",
            "name": "Alpha 7 IV (a7 IV)",
            "base_price": 215000,
            "colors": ["Black"],
            "variants": [
                {"lens": "Body Only", "price_adj": 0},
                {"lens": "with 24-70mm f/2.8 GM Lens", "price_adj": 45000},
            ],
            "specs": {
                "sensor": "33MP Full-Frame Exmor R BSI CMOS",
                "video": "4K 60p 10-bit 4:2:2 All-Intra S-Cinetone",
                "lens_mount": "Sony E-Mount",
                "stabilization": "5-axis In-Body SteadyShot (5.5 stops)",
                "iso": "100 - 51,200 (exp. 50-204,800)",
                "resolution": "7008 x 4672",
                "weight": "658 g",
            },
        },
        {
            "brand": "Sony",
            "name": "Alpha 6700",
            "base_price": 128000,
            "colors": ["Black"],
            "variants": [
                {"lens": "Body Only", "price_adj": 0},
                {"lens": "with 16-50mm PZ Kit", "price_adj": 12000},
            ],
            "specs": {
                "sensor": "26.0MP APS-C Exmor R BSI CMOS",
                "video": "4K 120p / 4K 60p 6K oversampled 10-bit",
                "lens_mount": "Sony E-Mount",
                "stabilization": "5-axis In-Body Image Stabilization (5.0 stops)",
                "iso": "100 - 32,000",
                "resolution": "6192 x 4128",
                "weight": "493 g",
            },
        },
        {
            "brand": "Sony",
            "name": "ZV-E10 II",
            "base_price": 84000,
            "colors": ["Black", "White"],
            "variants": [
                {"lens": "Body Only", "price_adj": 0},
                {"lens": "Vlogger Kit with Mic & Grip", "price_adj": 14000},
            ],
            "specs": {
                "sensor": "26.0MP APS-C BSI CMOS Sensor",
                "video": "4K 60p 10-bit 4:2:2 oversampled 5.6K",
                "lens_mount": "Sony E-Mount",
                "stabilization": "Active Mode Electronic + Lens OSS",
                "iso": "100 - 32,000",
                "resolution": "6192 x 4128",
                "weight": "377 g",
            },
        },
        {
            "brand": "Canon",
            "name": "EOS R6 Mark II",
            "base_price": 198000,
            "colors": ["Black"],
            "variants": [
                {"lens": "Body Only", "price_adj": 0},
                {"lens": "with RF 24-105mm f/4L IS USM", "price_adj": 38000},
            ],
            "specs": {
                "sensor": "24.2MP Full-Frame CMOS Sensor",
                "video": "4K 60p 10-bit 6K oversampled uncropped Canon Log 3",
                "lens_mount": "Canon RF Mount",
                "stabilization": "In-Body Image Stabilizer up to 8.0 stops",
                "iso": "100 - 102,400",
                "resolution": "6000 x 4000",
                "weight": "670 g",
            },
        },
        {
            "brand": "Canon",
            "name": "EOS R50",
            "base_price": 64000,
            "colors": ["Black", "White"],
            "variants": [
                {"lens": "with RF-S 18-45mm IS STM Lens", "price_adj": 0},
            ],
            "specs": {
                "sensor": "24.2MP APS-C CMOS Sensor",
                "video": "4K 30p 6K oversampled uncropped",
                "lens_mount": "Canon RF / RF-S",
                "stabilization": "Movie Digital IS",
                "iso": "100 - 32,000",
                "resolution": "6000 x 4000",
                "weight": "375 g Ultra-compact",
            },
        },
        {
            "brand": "Nikon",
            "name": "Z8",
            "base_price": 310000,
            "colors": ["Black"],
            "variants": [
                {"lens": "Body Only", "price_adj": 0},
                {"lens": "with Nikkor Z 24-120mm f/4 S", "price_adj": 42000},
            ],
            "specs": {
                "sensor": "45.7MP Full-Frame Stacked BSI CMOS",
                "video": "8.3K 60p N-RAW / 4K 120p ProRes RAW",
                "lens_mount": "Nikon Z Mount",
                "stabilization": "5-axis Sensor-Shift VR 6.0 stops Synchro",
                "iso": "64 - 25,600",
                "resolution": "8256 x 5504",
                "weight": "910 g Weather-sealed",
            },
        },
        {
            "brand": "Fujifilm",
            "name": "X-T5",
            "base_price": 155000,
            "colors": ["Black", "Silver Retro"],
            "variants": [
                {"lens": "Body Only", "price_adj": 0},
                {"lens": "with XF 18-55mm f/2.8-4 R LM OIS", "price_adj": 22000},
            ],
            "specs": {
                "sensor": "40.2MP APS-C X-Trans CMOS 5 HR",
                "video": "6.2K 30p 10-bit 4:2:2 / 4K 60p HQ",
                "lens_mount": "Fujifilm X Mount",
                "stabilization": "5-axis In-Body Image Stabilization 7.0 stops",
                "iso": "125 - 12,800",
                "resolution": "7728 x 5152",
                "weight": "557 g Vintage Dial Ergonomics",
            },
        },
        {
            "brand": "DJI",
            "name": "Osmo Pocket 3",
            "base_price": 52000,
            "colors": ["Black"],
            "variants": [
                {"kit": "Standard Package", "price_adj": 0},
                {"kit": "Creator Combo with Mic & Battery Grip", "price_adj": 14000},
            ],
            "specs": {
                "sensor": "1″ CMOS Pocket Gimbal Camera",
                "video": "4K 120p D-Log M 10-bit ActiveTrack 6.0",
                "lens_mount": "Integrated 20mm f/2.0 lens",
                "stabilization": "3-Axis Mechanical Gimbal",
                "iso": "50 - 6,400",
                "resolution": "3840 x 2160",
                "weight": "179 g Rotating 2″ OLED Touchscreen",
            },
        },
        {
            "brand": "GoPro",
            "name": "HERO12 Black",
            "base_price": 35000,
            "colors": ["Black"],
            "variants": [
                {"kit": "Standard Pack", "price_adj": 0},
                {"kit": "Enduro Battery + Dual Charger Pack", "price_adj": 4500},
            ],
            "specs": {
                "sensor": "27MP 8:7 Aspect Ratio CMOS",
                "video": "5.3K 60p / 4K 120p HDR Video + GP-Log",
                "lens_mount": "Integrated Ultra-wide with Max Lens Mod 2.0 support",
                "stabilization": "HyperSmooth 6.0 with 360° Horizon Lock",
                "iso": "100 - 6,400",
                "resolution": "5568 x 4872",
                "weight": "154 g Waterproof to 10m without housing",
            },
        },
    ],
    "accessories": [
        {
            "brand": "Anker",
            "name": "737 Power Bank (PowerCore 24K)",
            "base_price": 9200,
            "colors": ["Space Gray"],
            "variants": [{"power": "140W PD 3.1", "price_adj": 0}],
            "specs": {
                "capacity": "24,000 mAh",
                "power": "140W Max Fast Output PD 3.1",
                "type": "Smart Digital Display Laptop Power Bank",
                "port": "2x USB-C + 1x USB-A",
                "compatibility": "MacBook, Dell XPS, iPhone, Galaxy",
                "material": "Polycarbonate + Aluminum",
                "weight": "630 g",
            },
        },
        {
            "brand": "Anker",
            "name": "Nano II 65W GaN Charger",
            "base_price": 2800,
            "colors": ["Black", "White"],
            "variants": [{"power": "65W GaN II", "price_adj": 0}],
            "specs": {
                "power": "65W High-Speed GaN II",
                "type": "Ultra-Compact Foldable Wall Plug",
                "port": "USB-C",
                "compatibility": "MacBook Air/Pro, iPad Pro, iPhone, Galaxy",
                "material": "GaN Semiconductor",
                "weight": "112 g",
            },
        },
        {
            "brand": "Anker",
            "name": "622 Magnetic Battery (MagGo)",
            "base_price": 3600,
            "colors": ["Misty Blue", "Interstellar Gray", "Dolomite White"],
            "variants": [{"capacity": "5,000 mAh", "price_adj": 0}],
            "specs": {
                "capacity": "5,000 mAh",
                "power": "7.5W Wireless Magnetic Snap-on",
                "type": "Foldable Kickstand MagSafe Power Bank",
                "port": "USB-C bi-directional",
                "compatibility": "iPhone 12, 13, 14, 15 series",
                "material": "Soft-touch Silicone",
                "weight": "142 g",
            },
        },
        {
            "brand": "Apple",
            "name": "20W USB-C Power Adapter",
            "base_price": 1900,
            "colors": ["White"],
            "variants": [{"edition": "Standard Retail", "price_adj": 0}],
            "specs": {
                "power": "20W Power Delivery",
                "type": "Fast Charging Wall Plug",
                "port": "USB-C",
                "compatibility": "iPhone 8 through 15, iPad, Apple Watch",
                "material": "Fire-retardant Polycarbonate",
                "weight": "58 g",
            },
        },
        {
            "brand": "Apple",
            "name": "MagSafe Charger",
            "base_price": 3800,
            "colors": ["Silver"],
            "variants": [{"length": "1m", "price_adj": 0}],
            "specs": {
                "power": "15W Fast Wireless Charging",
                "length": "1.0m Braided Cable",
                "type": "Magnetic Wireless Charging Puck",
                "port": "USB-C",
                "compatibility": "iPhone 12-15, AirPods Pro MagSafe Case",
                "material": "Aluminum + Matte White Shield",
                "weight": "80 g",
            },
        },
        {
            "brand": "Apple",
            "name": "Magic Mouse",
            "base_price": 7500,
            "colors": ["White", "Black"],
            "variants": [{"edition": "Multi-Touch", "price_adj": 0}],
            "specs": {
                "type": "Wireless Multi-Touch Bluetooth Mouse",
                "battery": "Built-in rechargeable lithium-ion (> 1 month)",
                "port": "USB-C to Lightning / USB-C",
                "compatibility": "Mac with macOS 10.11 or later, iPadOS 13.4+",
                "material": "Smooth Glass Surface + Aluminum Base",
                "weight": "99 g",
            },
        },
        {
            "brand": "Samsung",
            "name": "45W Power Adapter Trio",
            "base_price": 3200,
            "colors": ["Black", "White"],
            "variants": [{"edition": "Trio 45W + 15W + 9W", "price_adj": 0}],
            "specs": {
                "power": "45W Super Fast Charging 2.0 (65W total max)",
                "type": "Multi-port GaN Wall Charger",
                "port": "2x USB-C + 1x USB-A",
                "compatibility": "Galaxy S24/S23 Ultra, Tab S9, Laptops",
                "material": "Reinforced PC",
                "weight": "138 g",
            },
        },
        {
            "brand": "Baseus",
            "name": "Blade 100W Power Bank",
            "base_price": 7200,
            "colors": ["Black"],
            "variants": [{"capacity": "20,000 mAh", "price_adj": 0}],
            "specs": {
                "capacity": "20,000 mAh",
                "power": "100W Ultra-slim High-Power Output",
                "type": "Laptop Slim Power Bank with Digital Status Screen",
                "port": "2x USB-C + 2x USB-A",
                "compatibility": "MacBook, ThinkPad, Dell, Steam Deck, Phones",
                "material": "Anti-slip Anodized Texture",
                "weight": "490 g (18mm ultra-thin)",
            },
        },
        {
            "brand": "Belkin",
            "name": "BoostCharge Pro 3-in-1 MagSafe Stand",
            "base_price": 12500,
            "colors": ["Black", "White"],
            "variants": [{"edition": "15W MagSafe Stand", "price_adj": 0}],
            "specs": {
                "power": "15W Fast Wireless MagSafe + 5W Watch + 5W AirPods",
                "type": "3-in-1 Fast Wireless Charging Tree",
                "port": "DC Power Supply Included",
                "compatibility": "iPhone 12-15, Apple Watch Ultra/Series 7+, AirPods",
                "material": "Stainless Steel Arms + Premium Weighted Base",
                "weight": "580 g",
            },
        },
        {
            "brand": "SanDisk",
            "name": "Extreme PRO 1TB Portable SSD",
            "base_price": 13200,
            "colors": ["Black with Orange Accent"],
            "variants": [{"capacity": "1TB", "price_adj": 0}],
            "specs": {
                "capacity": "1TB NVMe Solid State Drive",
                "power": "USB 3.2 Gen 2x2 Type-C",
                "speed": "Read up to 2000 MB/s, Write up to 2000 MB/s",
                "type": "Rugged External SSD",
                "water_resistance": "IP55 Dust & Water Resistant + 3m Drop Protection",
                "weight": "77 g Forged Aluminum Chasis",
            },
        },
        {
            "brand": "Logitech",
            "name": "MX Master 3S Wireless Mouse",
            "base_price": 9500,
            "colors": ["Graphite", "Pale Gray"],
            "variants": [{"edition": "Quiet Clicks 8000 DPI", "price_adj": 0}],
            "specs": {
                "sensor": "8,000 DPI Darkfield Laser Tracking on Glass",
                "type": "Ergonomic Bluetooth & Logi Bolt Wireless Mouse",
                "battery": "Up to 70 days rechargeable via USB-C",
                "port": "USB-C Fast Charging",
                "compatibility": "Windows, macOS, iPadOS, Linux, ChromeOS",
                "weight": "141 g MagSpeed Electromagnetic Wheel",
            },
        },
        {
            "brand": "Oraimo",
            "name": "Toast 10 Byte 10000mAh Powerbank",
            "base_price": 1350,
            "colors": ["Black", "White"],
            "variants": [{"capacity": "10,000 mAh", "price_adj": 0}],
            "specs": {
                "capacity": "10,000 mAh",
                "power": "12W Dual USB Fast Charge",
                "type": "Slim Textured Pocket Powerbank",
                "port": "Micro-USB + Type-C Input, 2x USB-A Output",
                "material": "Fireproof Multi-protect ABS",
                "weight": "225 g Multi-protection Safety System",
            },
        },
    ],
}

# The original 31 sample products preserved for --sample mode or test reconciliation.
SAMPLE_PRODUCTS = [
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy A15",
        "description": "Light Blue",
        "price": "12500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.5″ AMOLED",
            "chip": "Helio G99",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "200 g",
            "sim": "Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy A25",
        "description": "Blue Black",
        "price": "16500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.5″ AMOLED",
            "chip": "Exynos 1280",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "6GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "197 g",
            "sim": "Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Camon 20",
        "description": "Serenity Blue",
        "price": "9500.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "128GB",
            "display": "6.67″ AMOLED",
            "chip": "Dimensity 810",
            "camera": "64MP triple",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "190 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Infinix",
        "name": "Hot 40",
        "description": "Palm Blue",
        "price": "7200.00",
        "freshness": "fresh",
        "specs": {
            "storage": "64GB",
            "display": "6.78″ IPS LCD",
            "chip": "Helio G88",
            "camera": "50MP dual",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "199 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Nokia",
        "name": "105 Classic",
        "description": "Dark Blue",
        "price": "1800.00",
        "freshness": "expired",
        "specs": {
            "display": "1.8″ QVGA",
            "battery": "1,450 mAh",
            "n5g": "No",
            "year": "2023",
            "weight": "70 g",
            "sim": "Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "13 128GB",
        "description": "Starlight",
        "price": "52000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.1″ OLED",
            "chip": "A15 Bionic",
            "camera": "12MP dual",
            "battery": "3,240 mAh",
            "n5g": "Yes",
            "year": "2021",
            "weight": "174 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "11 64GB",
        "description": "Purple",
        "price": "34000.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "64GB",
            "display": "6.1″ Liquid Retina",
            "chip": "A13 Bionic",
            "camera": "12MP dual",
            "battery": "3,110 mAh",
            "n5g": "No",
            "year": "2019",
            "weight": "194 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Xiaomi",
        "name": "Redmi Note 13",
        "description": "Midnight Black",
        "price": "14000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.67″ AMOLED",
            "chip": "Snapdragon 685",
            "camera": "108MP triple",
            "battery": "5,000 mAh",
            "ram": "6GB",
            "n5g": "No",
            "year": "2024",
            "weight": "189 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Oppo",
        "name": "A78",
        "description": "Aqua Green",
        "price": "13500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.43″ AMOLED",
            "chip": "Dimensity 700",
            "camera": "50MP dual",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "180 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Huawei",
        "name": "Y9 Pro",
        "description": "Midnight Black",
        "price": "11000.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "64GB",
            "display": "6.59″ IPS LCD",
            "chip": "Helio P35",
            "camera": "48MP quad",
            "battery": "4,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2020",
            "weight": "197 g",
            "sim": "Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "accessories",
        "brand": "Anker",
        "name": "PowerBank 20000mAh",
        "description": "20000mAh, dual USB",
        "price": "2500.00",
        "freshness": "fresh",
        "specs": {
            "capacity": "20000mAh",
            "type": "Power bank",
            "power": "22.5W",
            "port": "USB-C + USB-A",
            "material": "Plastic",
        },
    },
    {
        "shop": 0,
        "category_slug": "accessories",
        "brand": "Baseus",
        "name": "USB-C Cable 2m",
        "description": "USB-C to USB-C, braided",
        "price": "450.00",
        "freshness": "fresh",
        "specs": {
            "length": "2m",
            "type": "USB-C to USB-C",
            "power": "100W",
            "material": "Nylon braided",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy S24",
        "description": "Onyx Black",
        "price": "78500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "256GB",
            "display": "6.2″ Dynamic AMOLED",
            "chip": "Exynos 2400",
            "camera": "50MP triple",
            "battery": "4,000 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2024",
            "weight": "168 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy Z Flip 5",
        "description": "Mint",
        "price": "92000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "256GB",
            "display": "6.7″ Foldable AMOLED",
            "chip": "Snapdragon 8 Gen 2",
            "camera": "12MP dual",
            "battery": "3,700 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "187 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "15 Pro 256GB",
        "description": "Natural Titanium",
        "price": "115000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "256GB",
            "display": "6.1″ OLED",
            "chip": "A17 Pro",
            "camera": "48MP triple",
            "battery": "3,274 mAh",
            "n5g": "Yes",
            "year": "2023",
            "weight": "187 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "14 128GB",
        "description": "Midnight",
        "price": "72000.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "128GB",
            "display": "6.1″ OLED",
            "chip": "A15 Bionic",
            "camera": "12MP dual",
            "battery": "3,279 mAh",
            "n5g": "Yes",
            "year": "2022",
            "weight": "172 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Spark 20",
        "description": "Gravity Black",
        "price": "8500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.6″ IPS LCD",
            "chip": "Helio G85",
            "camera": "50MP dual",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "190 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Xiaomi",
        "name": "Redmi 13C",
        "description": "Midnight Black",
        "price": "7800.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "64GB",
            "display": "6.74″ IPS LCD",
            "chip": "Helio G85",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "192 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "OnePlus",
        "name": "Nord CE 3",
        "description": "Aqua Surge",
        "price": "22000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.7″ AMOLED",
            "chip": "Snapdragon 782G",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "184 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Google",
        "name": "Pixel 8",
        "description": "Obsidian",
        "price": "68000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.2″ OLED",
            "chip": "Tensor G3",
            "camera": "50MP dual",
            "battery": "4,575 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "187 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "JBL",
        "name": "Tune 510BT",
        "description": "Blue",
        "price": "3200.00",
        "freshness": "fresh",
        "specs": {
            "type": "Over-ear",
            "battery": "40h",
            "driver": "32mm",
            "bluetooth": "5.0",
            "weight": "160 g",
        },
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Anker",
        "name": "Nano Charger 65W",
        "description": "GaN, USB-C",
        "price": "1800.00",
        "freshness": "fresh",
        "specs": {"power": "65W", "type": "GaN wall charger", "port": "USB-C"},
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Spigen",
        "name": "Galaxy S24 Case",
        "description": "Clear",
        "price": "650.00",
        "freshness": "almost_expired",
        "specs": {"material": "Silicone", "type": "Case"},
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Belkin",
        "name": "Screen Protector iPhone 15",
        "description": "Tempered glass, 2-pack",
        "price": "850.00",
        "freshness": "fresh",
        "specs": {"type": "Tempered glass", "material": "Glass"},
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Xiaomi",
        "name": "Mi Band 8",
        "description": "Midnight Black",
        "price": "2900.00",
        "freshness": "expired",
        "specs": {
            "display": "1.62″ AMOLED",
            "battery": "190 mAh",
            "weight": "27 g",
            "water_resistance": "5ATM",
        },
    },
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Oraimo",
        "name": "PowerBank 10000mAh",
        "description": "10000mAh, slim",
        "price": "1200.00",
        "freshness": "fresh",
        "specs": {
            "capacity": "10000mAh",
            "type": "Power bank",
            "port": "USB-C + USB-A",
        },
    },
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Oraimo",
        "name": "Bluetooth Speaker",
        "description": "Portable, 5W",
        "price": "1800.00",
        "freshness": "fresh",
        "specs": {
            "battery": "12h",
            "power": "5W",
            "bluetooth": "5.0",
            "type": "Portable speaker",
        },
    },
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Baseus",
        "name": "Car Charger 30W",
        "description": "Dual USB-C",
        "price": "700.00",
        "freshness": "almost_expired",
        "specs": {"power": "30W", "type": "Car charger", "port": "USB-C x2"},
    },
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Pova 5",
        "description": "Aurora Blue",
        "price": "10500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.8″ IPS LCD",
            "chip": "Helio G99",
            "camera": "50MP dual",
            "battery": "6,000 mAh",
            "ram": "8GB",
            "n5g": "No",
            "year": "2023",
            "weight": "220 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Infinix",
        "name": "Note 30",
        "description": "Interstellar Blue",
        "price": "9800.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.78″ AMOLED",
            "chip": "Helio G99",
            "camera": "64MP triple",
            "battery": "5,000 mAh",
            "ram": "6GB",
            "n5g": "No",
            "year": "2023",
            "weight": "204 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Nokia",
        "name": "G22",
        "description": "Meteor Gray",
        "price": "6500.00",
        "freshness": "expired",
        "specs": {
            "storage": "64GB",
            "display": "6.5″ IPS LCD",
            "chip": "Unisoc T606",
            "camera": "50MP triple",
            "battery": "5,050 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "196 g",
            "sim": "Dual Nano",
        },
    },
]


class Command(BaseCommand):
    help = "Seed realistic shops, procedural products across all 6 categories with public images."

    def add_arguments(self, parser):
        parser.add_argument(
            "--per-category",
            type=int,
            default=100,
            help="Number of products to generate per category (default: 100).",
        )
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Delete existing seeded products, images, and shops before seeding.",
        )
        parser.add_argument(
            "--no-images",
            action="store_true",
            help="Skip creating product images. Same as --images-per-product 0.",
        )
        parser.add_argument(
            "--images-per-product",
            type=int,
            default=len(PRODUCT_IMAGE_VIEWS),
            help=(
                "How many images to attach to each product, 0 to "
                f"{len(PRODUCT_IMAGE_VIEWS)} (default: {len(PRODUCT_IMAGE_VIEWS)}). "
                "The first is the primary."
            ),
        )
        parser.add_argument(
            "--force-images",
            action="store_true",
            help="Re-generate and overwrite primary images even if one already exists.",
        )
        parser.add_argument(
            "--image-source",
            choices=["auto", "unsplash", "pillow"],
            default="auto",
            help="Image source: 'auto' (try Unsplash, fallback to Pillow), 'unsplash' (only Unsplash), or 'pillow' (procedural offline).",
        )
        parser.add_argument(
            "--sample",
            action="store_true",
            help="Seed only the minimal 31 starter products for quick testing.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass the DEBUG safety check.",
        )

    def handle(self, *args, **options):
        # DiscoverRunner sets DEBUG = False for every test run regardless of the
        # settings module, so DEBUG on its own cannot tell production from the
        # test runner. A test database is as safe to seed as a dev one -- it is
        # created and destroyed inside the run.
        is_test_db = connection.settings_dict["NAME"].startswith("test_")
        if not settings.DEBUG and not is_test_db and not options.get("force", False):
            raise CommandError(
                "Seeding is only permitted when settings.DEBUG is True or "
                "--force is supplied."
            )

        per_category = options["per_category"]
        clean = options["clean"]
        force_images = options["force_images"]
        image_source = options["image_source"]
        sample_mode = options["sample"]

        images_per_product = options["images_per_product"]
        if not 0 <= images_per_product <= len(PRODUCT_IMAGE_VIEWS):
            raise CommandError(
                "--images-per-product must be between 0 and "
                f"{len(PRODUCT_IMAGE_VIEWS)}."
            )
        if options["no_images"]:
            images_per_product = 0

        # Ensure seed user exists
        user, user_created = User.objects.get_or_create(
            username=SEED_USER["username"],
            defaults={"email": SEED_USER["email"]},
        )
        if user_created:
            user.set_password(SEED_USER["password"])
            user.save()
            self.stdout.write(f"Created seed user: {user.username}")
        else:
            self.stdout.write(f"Exists seed user: {user.username}")

        # Handle --clean
        if clean:
            self.stdout.write("Cleaning previous seed products and shops...")
            Product.objects.filter(shop__owner=user).delete()
            Shop.objects.filter(owner=user).delete()
            self.stdout.write("Cleanup complete.")

        # Ensure shops exist. Sample mode is meant to be minimal, so it creates
        # only the shops its own products reference -- seeding all eight would
        # leave five with no listings at all, which the ops health page then
        # reports as unreachable shops that were never really there.
        shops_to_seed = SHOPS
        if sample_mode:
            shops_to_seed = SHOPS[: max(p["shop"] for p in SAMPLE_PRODUCTS) + 1]

        shop_objs = []
        for sdata in shops_to_seed:
            shop, created = Shop.objects.get_or_create(
                slug=sdata["slug"],
                defaults={
                    "name": sdata["name"],
                    "city": sdata["city"],
                    "phone": sdata["phone"],
                    "description": sdata["description"],
                    "latitude": sdata.get("latitude"),
                    "longitude": sdata.get("longitude"),
                    "is_verified": sdata.get("is_verified", False),
                    "owner": user,
                },
            )
            shop_objs.append(shop)

        if sample_mode:
            self.stdout.write("Running in sample mode (31 products across 3 shops)...")
            self._seed_sample(shop_objs, images_per_product, force_images, image_source)
            return

        # Ensure all 6 categories exist
        category_cache = {}
        category_order = [
            ("phones", "Phones", 0),
            ("laptops", "Laptops", 1),
            ("audio", "Audio", 2),
            ("tablets", "Tablets", 3),
            ("cameras", "Cameras", 4),
            ("accessories", "Accessories", 5),
        ]
        for cslug, cname, corder in category_order:
            cat, _ = Category.objects.get_or_create(
                slug=cslug,
                defaults={"name": cname, "display_order": corder},
            )
            category_cache[cslug] = cat

        ensure_taxonomy()
        for cat in category_cache.values():
            cat.refresh_from_db(fields=["product_type"])

        total_created = 0
        total_updated = 0
        total_images = 0

        self.stdout.write(
            f"Seeding catalog matrix: target ~{per_category} products per category across {len(category_cache)} categories..."
        )

        for cat_slug, category in category_cache.items():
            models_list = CATALOG_MATRICES.get(cat_slug, [])
            if not models_list:
                continue

            cat_created = 0
            cat_updated = 0

            # Generate exactly `per_category` items by cycling through model families and variants
            for idx in range(per_category):
                model_def = models_list[idx % len(models_list)]
                brand = model_def["brand"]
                base_name = model_def["name"]
                base_price = Decimal(str(model_def["base_price"]))

                # Variation attributes
                variants = model_def.get("variants", [{}])
                variant_cfg = variants[idx % len(variants)]
                colors = model_def.get(
                    "colors", ["Black", "Silver", "Space Gray", "Midnight"]
                )
                color = colors[idx % len(colors)]

                # Build variant title, description and adjusted price
                price_adj = Decimal(str(variant_cfg.get("price_adj", 0)))
                final_price = base_price + price_adj

                # Condition: 70% New, 20% Used, 10% Refurbished
                cond_seed = (idx * 7 + 3) % 10
                if cond_seed in (0, 1):
                    condition = ProductCondition.USED
                    final_price = (final_price * Decimal("0.78")).quantize(
                        Decimal("100.00")
                    )
                    cond_note = "Used - Very Good"
                elif cond_seed == 2:
                    condition = ProductCondition.REFURBISHED
                    final_price = (final_price * Decimal("0.88")).quantize(
                        Decimal("100.00")
                    )
                    cond_note = "Refurbished - Certified"
                else:
                    condition = ProductCondition.NEW
                    cond_note = "Brand New - Factory Sealed"

                # Freshness: 75% fresh, 15% almost expired, 10% expired
                fresh_seed = (idx * 11 + 5) % 20
                if fresh_seed in (0, 1, 2):
                    freshness = "almost_expired"
                elif fresh_seed in (3, 4):
                    freshness = "expired"
                else:
                    freshness = "fresh"

                last_confirmed_at = timezone.now() - SEED_CONFIRMATION_AGE[freshness]

                # Specific variant name and description
                spec_summary_parts = []
                if "storage" in variant_cfg:
                    spec_summary_parts.append(variant_cfg["storage"])
                if "ram" in variant_cfg:
                    spec_summary_parts.append(variant_cfg["ram"])
                if "processor" in variant_cfg:
                    spec_summary_parts.append(variant_cfg["processor"])
                if "lens" in variant_cfg:
                    spec_summary_parts.append(variant_cfg["lens"])
                if "kit" in variant_cfg:
                    spec_summary_parts.append(variant_cfg["kit"])
                if "capacity" in variant_cfg:
                    spec_summary_parts.append(variant_cfg["capacity"])
                if "power" in variant_cfg:
                    spec_summary_parts.append(variant_cfg["power"])

                variant_spec_str = (
                    " · ".join(spec_summary_parts) if spec_summary_parts else ""
                )

                if variant_spec_str:
                    item_name = f"{base_name} ({variant_spec_str})"
                else:
                    item_name = base_name

                item_description = f"{color} · {cond_note}"

                # Merge model specs with variant overrides
                full_specs = dict(model_def.get("specs", {}))
                for k, v in variant_cfg.items():
                    if k not in ("price_adj", "edition"):
                        full_specs[k] = v

                # Distribute products across appropriate shops
                # Cameras prefer shop 4 (Piassa Camera & Sound)
                if cat_slug == "cameras" and (idx % 3 == 0):
                    shop = shop_objs[4]
                elif cat_slug in ("laptops", "tablets") and (idx % 4 == 0):
                    shop = shop_objs[3]  # Megenagna Digital
                elif cat_slug == "accessories" and (idx % 4 == 0):
                    shop = shop_objs[1]  # Merkato Wholesale
                else:
                    shop = shop_objs[idx % len(shop_objs)]

                # Deterministic unique slug
                slug_source = f"{shop.slug}-{brand}-{base_name}-{color}-{idx}"
                product_slug = slugify(slug_source)[:190]

                product, created = Product.objects.update_or_create(
                    slug=product_slug,
                    defaults={
                        "shop": shop,
                        "category": category,
                        "brand": brand,
                        "name": item_name[:200],
                        "description": item_description,
                        "price": final_price,
                        "currency": "ETB",
                        "condition": condition,
                        "status": ProductStatus.LIVE,
                        "is_available": True,
                        "stock": (idx % 8) + 1,
                        "views": 20 + ((idx * 37) % 450),
                        "inquiries": (idx * 5) % 28,
                        "specs": seed_specs(
                            category,
                            full_specs,
                            text=f"{brand} {item_name} {color}",
                            condition=condition,
                            seed=product_slug,
                        ),
                        "last_confirmed_at": last_confirmed_at,
                        # Deliberately not writing confirmation_status. It is
                        # the sweep's output, and `last_confirmed_at` above is
                        # aged up to 45 days back -- stamping FRESH on top of
                        # that manufactures exactly the stored-vs-derived drift
                        # the ops reconciliation page exists to catch.
                    },
                )

                if created:
                    cat_created += 1
                    total_created += 1
                else:
                    cat_updated += 1
                    total_updated += 1

                total_images += self._attach_images(
                    product,
                    category_slug=cat_slug,
                    brand=brand,
                    name=base_name,
                    spec_summary=variant_spec_str or color,
                    variant_idx=idx,
                    images_per_product=images_per_product,
                    force_images=force_images,
                    image_source=image_source,
                )

            self.stdout.write(
                f"[{cat_slug.upper()}] Done: {cat_created} created, {cat_updated} updated."
            )

        self.stdout.write(
            f"Successfully seeded catalog: {total_created} new products, {total_updated} updated, "
            f"{total_images} images attached ({Product.objects.count()} total in database)."
        )

    def _attach_images(
        self,
        product,
        *,
        category_slug,
        brand,
        name,
        spec_summary,
        variant_idx,
        images_per_product,
        force_images,
        image_source,
    ):
        """Attach the product's gallery. Returns how many rows were written."""
        if images_per_product < 1:
            return 0

        existing = list(product.product_image.all())
        if existing:
            if not force_images:
                return 0
            # Clear the whole gallery, not just the primary: a partial clear
            # leaves the old non-primary rows behind and stacks a second gallery
            # on top. Deleting row by row lets ProductImage's post_delete
            # receiver take the files with them; a queryset .delete() would not.
            for image_row in existing:
                image_row.delete()

        written = 0
        for order, view in enumerate(PRODUCT_IMAGE_VIEWS[:images_per_product]):
            img_bytes = get_category_image_bytes(
                category_slug=category_slug,
                brand=brand,
                name=name,
                spec_summary=spec_summary,
                variant_idx=variant_idx,
                source_mode=image_source,
                view=view,
            )
            img_obj = ProductImage(
                product=product,
                is_primary=(order == 0),
                display_order=order,
            )
            img_obj.image.save(
                f"{product.slug[:32]}-{view}.jpg",
                ContentFile(img_bytes),
                save=True,
            )
            written += 1
        return written

    def _seed_sample(self, shop_objs, images_per_product, force_images, image_source):
        """Seeds the minimal 31 starter products for tests and quick dev runs."""
        count = 0
        updated = 0
        img_count = 0

        ensure_taxonomy()
        for data in SAMPLE_PRODUCTS:
            shop = shop_objs[data["shop"]]
            category = Category.objects.get(slug=data["category_slug"])
            slug = f"{shop.slug}-{data['brand']}-{data['name']}".lower().replace(
                " ", "-"
            )
            specs = seed_specs(
                category,
                data["specs"],
                text=f"{data['brand']} {data['name']} {data.get('description', '')}",
                condition=ProductCondition.NEW,
                seed=slug,
            )

            product, created = Product.objects.update_or_create(
                slug=slug,
                defaults={
                    "shop": shop,
                    "category": category,
                    "brand": data["brand"],
                    "name": data["name"],
                    "description": data.get("description", ""),
                    "price": data["price"],
                    "specs": specs,
                    "last_confirmed_at": timezone.now()
                    - SEED_CONFIRMATION_AGE[data.get("freshness", "fresh")],
                },
            )
            if created:
                count += 1
            else:
                updated += 1

            img_count += self._attach_images(
                product,
                category_slug=data["category_slug"],
                brand=data["brand"],
                name=data["name"],
                spec_summary=data.get("description", ""),
                variant_idx=count + updated,
                images_per_product=images_per_product,
                force_images=force_images,
                image_source=image_source,
            )

        self.stdout.write(
            f"Sample mode completed: Created {count} new, updated {updated} "
            f"({Product.objects.count()} total, {img_count} images attached)"
        )
