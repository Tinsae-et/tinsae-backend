"""The dashboard's freshness transitions.

The AC that matters most here is "web path calls the same service the bot will".
`test_web_confirm_matches_a_direct_service_call` is the one that actually holds
that line: it confirms one product over HTTP and an identical one by calling
`apply_confirmation` directly, then asserts the two rows agree field by field.
Any logic that creeps into the view instead of the service breaks it.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.freshness import Freshness
from catalog.models import Category, ConfirmationStatus, Product, ProductStatus
from catalog.services import apply_confirmation
from users.models import User

from .models import Shop

STALE_AGE = timedelta(days=40)


class ShopProductTransitionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="merchant", password="pass")
        self.shop = Shop.objects.create(name="Owner Shop", owner=self.owner)
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _product(self, name, **overrides):
        fields = {
            "shop": self.shop,
            "category": self.category,
            "brand": "Acme",
            "name": name,
            "price": "1000.00",
            "last_confirmed_at": timezone.now() - STALE_AGE,
            "confirmation_status": ConfirmationStatus.STALE,
        }
        fields.update(overrides)
        return Product.objects.create(**fields)

    def _url(self, product, suffix=""):
        return f"/api/v1/shops/{self.shop.slug}/products/{product.slug}/{suffix}"

    # --- the derived tier is on the dashboard payload --------------------

    def test_list_exposes_the_derived_tier(self):
        self._product("just-confirmed", last_confirmed_at=timezone.now())
        self._product("long-gone")
        self._product("never", last_confirmed_at=None)

        response = self.client.get(f"/api/v1/shops/{self.shop.slug}/products/")

        self.assertEqual(response.status_code, 200)
        tiers = {row["name"]: row["freshness"] for row in response.data["results"]}
        self.assertEqual(tiers["just-confirmed"], Freshness.FRESH)
        self.assertEqual(tiers["long-gone"], Freshness.EXPIRED)
        self.assertEqual(tiers["never"], Freshness.EXPIRED)

    def test_tier_is_read_only(self):
        product = self._product("phone")
        response = self.client.patch(
            self._url(product), {"freshness": "fresh"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.freshness, Freshness.EXPIRED)

    # --- transitions ------------------------------------------------------

    def test_confirm_restores_freshness(self):
        product = self._product("phone")

        response = self.client.post(self._url(product, "confirm/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["freshness"], Freshness.FRESH)
        product.refresh_from_db()
        self.assertEqual(product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertTrue(product.is_available)

    def test_mark_sold_restarts_the_clock_and_hides_the_listing(self):
        # "Out of stock" is fresh information, not the absence of it -- so the
        # product goes unavailable *and* fresh. Deliberate, and documented in
        # catalog/services.py.
        product = self._product("phone")

        response = self.client.post(self._url(product, "mark-sold/"))

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertFalse(product.is_available)
        self.assertEqual(product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertEqual(product.freshness, Freshness.FRESH)

    def test_pause_hides_the_listing_without_touching_the_clock(self):
        # The distinction from mark-sold: pausing makes no claim about stock, so
        # it must not launder a stale listing fresh.
        product = self._product("phone")
        before = product.last_confirmed_at

        response = self.client.post(self._url(product, "pause/"))

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertFalse(product.is_available)
        self.assertEqual(product.last_confirmed_at, before)
        self.assertEqual(product.freshness, Freshness.EXPIRED)

    def test_resume_brings_it_back_without_restarting_the_clock(self):
        # The mirror of pause, and the reason pause is safe. Un-pausing retracts
        # no stock claim, so a listing that was stale going in is still stale
        # coming out -- otherwise the pair is a two-click freshness reset on a
        # listing of any age.
        product = self._product("phone", is_available=False)
        before = product.last_confirmed_at

        response = self.client.post(self._url(product, "resume/"))

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertTrue(product.is_available)
        self.assertEqual(product.last_confirmed_at, before)
        self.assertEqual(product.freshness, Freshness.EXPIRED)

    def test_resume_then_confirm_is_how_a_listing_comes_back_fresh(self):
        # What the above leaves open, and the point of splitting them: coming
        # back fresh takes the same tap it would have taken without the pause.
        product = self._product("phone", is_available=False)

        self.client.post(self._url(product, "resume/"))
        response = self.client.post(self._url(product, "confirm/"))

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertTrue(product.is_available)
        self.assertEqual(product.freshness, Freshness.FRESH)

    # --- the AC: no divergent logic ---------------------------------------

    def test_web_confirm_matches_a_direct_service_call(self):
        via_web = self._product("via-web")
        via_service = self._product("via-service")

        response = self.client.post(self._url(via_web, "confirm/"))
        self.assertEqual(response.status_code, 200)
        apply_confirmation(via_service, confirmed=True)

        via_web.refresh_from_db()
        via_service.refresh_from_db()

        self.assertEqual(via_web.confirmation_status, via_service.confirmation_status)
        self.assertEqual(via_web.is_available, via_service.is_available)
        self.assertEqual(via_web.freshness, via_service.freshness)
        # Same instant to the second: both went through one writer, moments apart.
        self.assertLess(
            abs(via_web.last_confirmed_at - via_service.last_confirmed_at),
            timedelta(seconds=5),
        )

    # --- confirm all -------------------------------------------------------

    def _confirm_all(self, slugs):
        return self.client.post(
            f"/api/v1/shops/{self.shop.slug}/products/confirm-all/",
            {"slugs": slugs},
            format="json",
        )

    def test_confirm_all_confirms_every_named_product(self):
        a = self._product("one")
        b = self._product("two")
        untouched = self._product("three")

        response = self._confirm_all([a.slug, b.slug])

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.data["confirmed"], [a.slug, b.slug])
        self.assertEqual(response.data["not_found"], [])
        a.refresh_from_db()
        b.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(a.freshness, Freshness.FRESH)
        self.assertEqual(b.freshness, Freshness.FRESH)
        self.assertEqual(untouched.freshness, Freshness.EXPIRED)

    def test_confirm_all_reports_partial_success(self):
        real = self._product("one")

        response = self._confirm_all([real.slug, "no-such-product"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["confirmed"], [real.slug])
        self.assertEqual(response.data["not_found"], ["no-such-product"])
        real.refresh_from_db()
        self.assertEqual(real.freshness, Freshness.FRESH)

    def test_confirm_all_rejects_an_empty_body(self):
        for body in ({}, {"slugs": []}, {"slugs": "not-a-list"}):
            with self.subTest(body=body):
                response = self.client.post(
                    f"/api/v1/shops/{self.shop.slug}/products/confirm-all/",
                    body,
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("slugs", response.data)

    def test_confirm_all_is_capped(self):
        response = self._confirm_all([f"slug-{n}" for n in range(101)])
        self.assertEqual(response.status_code, 400)
        self.assertIn("slugs", response.data)

    def test_confirm_all_deduplicates(self):
        product = self._product("one")
        response = self._confirm_all([product.slug, product.slug])
        self.assertEqual(response.data["confirmed"], [product.slug])

    # --- query filters & sorting ------------------------------------------

    def test_filter_fresh_true(self):
        self._product("fresh-one", last_confirmed_at=timezone.now())
        self._product("stale-one", last_confirmed_at=timezone.now() - STALE_AGE)

        response = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?fresh=true"
        )
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.data["results"]]
        self.assertIn("fresh-one", names)
        self.assertNotIn("stale-one", names)

    def test_filter_fresh_false_needs_confirming(self):
        self._product("fresh-one", last_confirmed_at=timezone.now())
        self._product("stale-one", last_confirmed_at=timezone.now() - STALE_AGE)
        self._product("never-confirmed", last_confirmed_at=None)

        for param in ("false", "needs_confirming", "needs-attention"):
            with self.subTest(param=param):
                response = self.client.get(
                    f"/api/v1/shops/{self.shop.slug}/products/?fresh={param}"
                )
                self.assertEqual(response.status_code, 200)
                names = [p["name"] for p in response.data["results"]]
                self.assertNotIn("fresh-one", names)
                self.assertIn("stale-one", names)
                self.assertIn("never-confirmed", names)

    def test_sort_by_last_confirmed_at(self):
        p1 = self._product("older", last_confirmed_at=timezone.now() - timedelta(days=5))
        p2 = self._product("newer", last_confirmed_at=timezone.now() - timedelta(days=1))

        response = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?ordering=last_confirmed_at"
        )
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.data["results"]]
        self.assertEqual(names, ["older", "newer"])

        response_desc = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?ordering=-last_confirmed_at"
        )
        self.assertEqual(response_desc.status_code, 200)
        names_desc = [p["name"] for p in response_desc.data["results"]]
        self.assertEqual(names_desc, ["newer", "older"])

    def test_filter_by_category(self):
        other_cat = Category.objects.create(name="Laptops", slug="laptops")
        self._product("phone-item", category=self.category)
        self._product("laptop-item", category=other_cat)

        response = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?category=laptops"
        )
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.data["results"]]
        self.assertEqual(names, ["laptop-item"])

    def test_filter_by_status(self):
        self._product("live-item", status=ProductStatus.LIVE, is_available=True)
        self._product("sold-item", status=ProductStatus.SOLD, is_available=False)
        self._product("draft-item", status=ProductStatus.DRAFT, is_available=True)

        res_live = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?status=live"
        )
        self.assertEqual([p["name"] for p in res_live.data["results"]], ["live-item"])

        res_sold = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?status=sold"
        )
        self.assertEqual([p["name"] for p in res_sold.data["results"]], ["sold-item"])

        res_draft = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?status=draft"
        )
        self.assertEqual([p["name"] for p in res_draft.data["results"]], ["draft-item"])

    def test_search_by_query(self):
        self._product("iPhone 14 Pro", description="Great phone")
        self._product("Samsung Galaxy", description="Android flagship")

        response = self.client.get(
            f"/api/v1/shops/{self.shop.slug}/products/?q=iPhone"
        )
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.data["results"]]
        self.assertIn("iPhone 14 Pro", names)
        self.assertNotIn("Samsung Galaxy", names)

