"""Cross-shop isolation audit.

One table, every shop-facing endpoint, three callers: the owner, a different
merchant, and nobody. Table-driven on purpose -- `test_every_shop_route_is_audited`
walks the URLconf and fails if a route exists that no row covers, so adding an
endpoint without adding a row breaks the build rather than quietly widening the
surface.

The rule this suite encodes, which the ticket leaves open:

    404 when the caller should not know the resource exists (anything nested
    under a shop they do not own -- the queryset is owner-scoped, so it is
    genuinely absent).

    403 when the resource is public but the action is not (the shop profile is
    readable by anyone; only writing it is refused).

Both satisfy "403/404", but mixing them arbitrarily would leak existence through
the difference, so each row states which one it expects and why.
"""

import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import get_resolver
from PIL import Image
from rest_framework.test import APIClient

from catalog.models import Category, Product, ProductImage
from users.models import User

from .models import Shop

# Uploads are real file writes. Redirect them at a temp dir so the suite does not
# litter MEDIA_ROOT.
MEDIA = tempfile.mkdtemp()


def _png():
    """A genuinely valid 1x1 PNG -- `validate_image_file` opens it with Pillow."""
    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("px.png", buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA)
class CrossShopIsolationTests(TestCase):
    """Every shop-scoped endpoint, attempted by the wrong merchant."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pass")
        self.intruder = User.objects.create_user(username="intruder", password="pass")

        self.shop = Shop.objects.create(name="Owner Shop", owner=self.owner)
        self.other_shop = Shop.objects.create(name="Intruder Shop", owner=self.intruder)
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Acme",
            name="Target",
            price="1000.00",
        )
        self.image = ProductImage.objects.create(product=self.product, image=_png())

    # --- the table ---------------------------------------------------------

    def _routes(self):
        base = f"/api/v1/shops/{self.shop.slug}/products"
        product = f"{base}/{self.product.slug}"
        images = f"{product}/images"
        # Order matters: `test_the_owner_is_not_locked_out_by_any_of_it` walks
        # this list in sequence against a live product, so the two destructive
        # rows come last -- image before product, since deleting the product
        # cascades its images.
        create = {"brand": "X", "name": "N", "price": "1.00", "category": "phones"}
        return [
            ("get", f"{base}/", None),
            ("post", f"{base}/", create),
            ("post", f"{base}/confirm-all/", {"slugs": [self.product.slug]}),
            ("get", f"{product}/", None),
            ("patch", f"{product}/", {"name": "stolen"}),
            ("put", f"{product}/", create),
            ("post", f"{product}/confirm/", None),
            ("post", f"{product}/mark-sold/", None),
            ("post", f"{product}/pause/", None),
            ("post", f"{product}/resume/", None),
            ("get", f"{images}/", None),
            ("get", f"{images}/{self.image.pk}/", None),
            ("patch", f"{images}/{self.image.pk}/", {"display_order": 9}),
            ("delete", f"{images}/{self.image.pk}/", None),
            ("delete", f"{product}/", None),
        ]

    def _call(self, client, method, url, body):
        fn = getattr(client, method)
        return fn(url, body, format="json") if body is not None else fn(url)

    def test_another_merchant_gets_404_everywhere(self):
        client = APIClient()
        client.force_authenticate(self.intruder)
        for method, url, body in self._routes():
            with self.subTest(route=f"{method.upper()} {url}"):
                response = self._call(client, method, url, body)
                self.assertEqual(
                    response.status_code,
                    404,
                    f"{method.upper()} {url} returned {response.status_code}",
                )

    def test_anonymous_gets_401_everywhere(self):
        client = APIClient()
        for method, url, body in self._routes():
            with self.subTest(route=f"{method.upper()} {url}"):
                response = self._call(client, method, url, body)
                self.assertEqual(
                    response.status_code,
                    401,
                    f"{method.upper()} {url} returned {response.status_code}",
                )

    def test_the_owner_is_not_locked_out_by_any_of_it(self):
        # The mirror of the two above: a suite that only asserts refusals passes
        # just as well against an endpoint that refuses everyone.
        client = APIClient()
        client.force_authenticate(self.owner)
        for method, url, body in self._routes():
            with self.subTest(route=f"{method.upper()} {url}"):
                response = self._call(client, method, url, body)
                self.assertNotIn(
                    response.status_code,
                    (401, 403, 404),
                    f"{method.upper()} {url} refused the owner "
                    f"with {response.status_code}",
                )

    # --- nothing is mutated by a failed attempt ---------------------------

    def test_a_refused_attempt_changes_nothing(self):
        client = APIClient()
        client.force_authenticate(self.intruder)
        before = {
            "name": self.product.name,
            "available": self.product.is_available,
            "confirmed": self.product.last_confirmed_at,
        }

        for method, url, body in self._routes():
            self._call(client, method, url, body)

        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, before["name"])
        self.assertEqual(self.product.is_available, before["available"])
        self.assertEqual(self.product.last_confirmed_at, before["confirmed"])
        self.assertTrue(ProductImage.objects.filter(pk=self.image.pk).exists())

    # --- confirm-all cannot reach across shops ----------------------------

    def test_confirm_all_ignores_slugs_from_another_shop(self):
        # The subtle one: the intruder uses *their own* shop URL, which they are
        # entitled to, but names a product belonging to someone else.
        client = APIClient()
        client.force_authenticate(self.intruder)

        response = client.post(
            f"/api/v1/shops/{self.other_shop.slug}/products/confirm-all/",
            {"slugs": [self.product.slug]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["confirmed"], [])
        self.assertEqual(response.data["not_found"], [self.product.slug])
        self.product.refresh_from_db()
        self.assertIsNone(self.product.last_confirmed_at)


class ShopProfileIsolationTests(TestCase):
    """The shop profile is the one endpoint that is public to read."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pass")
        self.intruder = User.objects.create_user(username="intruder", password="pass")
        self.shop = Shop.objects.create(
            name="Owner Shop", owner=self.owner, telegram_chat_id="123456"
        )

    def test_another_merchant_cannot_edit_the_profile(self):
        client = APIClient()
        client.force_authenticate(self.intruder)
        response = client.patch(
            f"/api/v1/shops/{self.shop.slug}/", {"name": "Stolen"}, format="json"
        )
        # 403 rather than 404 here, and deliberately: the shop is public, so
        # pretending it does not exist would contradict the storefront.
        self.assertEqual(response.status_code, 403)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.name, "Owner Shop")

    def test_the_public_read_does_not_leak_the_bot_address(self):
        for client, label in (
            (APIClient(), "anonymous"),
            (APIClient(), "another merchant"),
        ):
            if label == "another merchant":
                client.force_authenticate(self.intruder)
            with self.subTest(caller=label):
                response = client.get(f"/api/v1/shops/{self.shop.slug}/")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("telegram_chat_id", response.data)
                self.assertNotIn("owner", response.data)

    def test_the_owner_still_sees_their_own_bot_address(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.get(f"/api/v1/shops/{self.shop.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("telegram_chat_id", response.data)


class AuditCoverageTests(TestCase):
    """The suite has to keep up with the URLconf."""

    def test_every_shop_nested_route_is_covered_by_the_table(self):
        patterns = []

        def walk(resolver, prefix=""):
            for entry in resolver.url_patterns:
                if hasattr(entry, "url_patterns"):
                    walk(entry, prefix + str(entry.pattern))
                else:
                    patterns.append(prefix + str(entry.pattern))

        walk(get_resolver())
        nested = {
            p
            for p in patterns
            if "shops/<slug:shop_slug>/products" in p.replace("^", "")
        }

        # One row per route in CrossShopIsolationTests._routes(), collapsed to
        # the distinct URL shapes it exercises.
        covered = {
            "products/",
            "products/confirm-all/",
            "products/<slug:slug>/",
            "products/<slug:slug>/confirm/",
            "products/<slug:slug>/mark-sold/",
            "products/<slug:slug>/pause/",
            "products/<slug:slug>/resume/",
            "products/<slug:product_slug>/images/",
            "products/<slug:product_slug>/images/<int:pk>/",
        }
        found = {p.split("shops/<slug:shop_slug>/")[-1] for p in nested}

        self.assertEqual(
            found - covered,
            set(),
            "New shop-nested route(s) with no isolation-audit row. Add them to "
            "CrossShopIsolationTests._routes().",
        )
