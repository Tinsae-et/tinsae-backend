from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Category, Product
from shops.models import Shop
from users.models import User


class ProductApiTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="test_merchant", password="pass")
        self.shop = Shop.objects.create(
            name="Test Shop", slug="test-shop", owner=self.owner
        )
        self.cat = Category.objects.create(name="Phones", slug="phones")
        self.product = Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Samsung",
            name="Galaxy A15",
            slug="galaxy-a15",
            price="12500.00",
            specs={"ram": "4GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )

    def test_product_list_is_paginated_and_contains_product(self):
        response = APIClient().get("/api/v1/catalog/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["slug"], "galaxy-a15")

    def test_filter_by_category_slug(self):
        acc = Category.objects.create(name="Accessories", slug="accessories")
        Product.objects.create(
            category=acc,
            shop=self.shop,
            brand="Anker",
            name="PowerPort",
            slug="powerport",
            price="450.00",
        )

        response = APIClient().get("/api/v1/catalog/products/?category=phones")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Samsung Galaxy A15")

    def test_product_detail_by_slug(self):
        response = APIClient().get("/api/v1/catalog/products/galaxy-a15/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["specs"], {"ram": "4GB", "storage": "128GB"})

    def test_search_keyword_match(self):
        response = APIClient().get("/api/v1/catalog/products/?q=gala")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["title"], "Samsung Galaxy A15")

    def test_max_price_filter(self):
        response = APIClient().get("/api/v1/catalog/products/?max_price=20000.00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(float(response.data["results"][0]["price"]), 12500.00)

    def test_min_price_filter(self):
        response = APIClient().get("/api/v1/catalog/products/?min_price=10000.00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(float(response.data["results"][0]["price"]), 12500.00)

    def test_vefified_only_filter(self):
        owner1 = User.objects.create_user(username="test_merchant1", password="pass")

        shop1 = Shop.objects.create(
            name="Test Shop1", slug="test-shop1", owner=owner1, is_verified=True
        )
        cat = Category.objects.create(name="Phones1", slug="phones1")
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A55",
            slug="galaxy-a55",
            price="32500.00",
            specs={"ram": "12GB", "storage": "128GB"},
        )

        respose = APIClient().get("/api/v1/catalog/products/?verified_only=ok")
        self.assertEqual(respose.status_code, 200)
        self.assertEqual(respose.data["results"][0]["title"], "Samsung Galaxy A55")

    def test_freshness_filter(self):
        owner1 = User.objects.create_user(username="test_merchant1", password="pass")

        shop1 = Shop.objects.create(
            name="Test Shop1", slug="test-shop1", owner=owner1, is_verified=True
        )
        cat = Category.objects.create(name="Phones1", slug="phones1")
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A55",
            slug="galaxy-a55",
            price="45500.00",
            specs={"ram": "12GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )

        # Stale products must drop out of the filtered list. Without this the
        # assertion below passes on the default value alone and never exercises
        # the filter.
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Nokia",
            name="Stale Phone",
            slug="stale-phone",
            price="5000.00",
            specs={},
            last_confirmed_at=timezone.now() - timedelta(days=60),
            confirmation_status="stale",
        )

        response = APIClient().get("/api/v1/catalog/products/?freshness=ok")
        self.assertEqual(response.status_code, 200)
        titles = [r["title"] for r in response.data["results"]]
        self.assertNotIn("Nokia Stale Phone", titles)
        self.assertEqual(len(response.data["results"]), 2)

    def test_combined_filtering(self):
        owner1 = User.objects.create_user(username="test_merchant1", password="pass")

        shop1 = Shop.objects.create(
            name="Test Shop1", slug="test-shop1", owner=owner1, is_verified=True
        )
        cat = Category.objects.create(name="Phones1", slug="phones1")
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A55",
            slug="galaxy-a55",
            price="45500.00",
            specs={"ram": "12GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A35",
            slug="galaxy-a35",
            price="32500.00",
            specs={"ram": "6GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )

        response = APIClient().get(
            "/api/v1/catalog/products/?q=gala&min_price=20000&max_price=35000&freshness=fresh&verified_only=ok&category=phones1"
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data["results"][0]["title"], "Samsung Galaxy A35")

    def _create_cheaper_product(self):
        return Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Samsung",
            name="Galaxy J2",
            slug="galaxy-j2",
            price="2000.00",
            specs={},
        )

    def test_ordering_by_price_ascending(self):
        self._create_cheaper_product()
        response = APIClient().get("/api/v1/catalog/products/?ordering=price")
        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data["results"]]
        self.assertEqual(titles, ["Samsung Galaxy J2", "Samsung Galaxy A15"])

    def test_ordering_by_price_descending(self):
        self._create_cheaper_product()
        response = APIClient().get("/api/v1/catalog/products/?ordering=-price")
        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data["results"]]
        self.assertEqual(titles, ["Samsung Galaxy A15", "Samsung Galaxy J2"])

    def test_invalid_ordering_is_rejected(self):
        # The allowlist is what keeps an arbitrary query param out of order_by(),
        # so anything outside it is a client error, not a silent fallback to the
        # ranking -- that would hide the caller's bug.
        for value in ("__secret_field__", "price;DROP TABLE"):
            with self.subTest(ordering=value):
                response = APIClient().get(
                    f"/api/v1/catalog/products/?ordering={value}"
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("ordering", response.data)


class ProductRankingTest(TestCase):
    """TIN-31 — fresh > stale > unconfirmed, with recency breaking ties."""

    def setUp(self):
        self.owner = User.objects.create_user(username="rank_merchant", password="pass")
        self.shop = Shop.objects.create(
            name="Rank Shop", slug="rank-shop", owner=self.owner, is_verified=True
        )
        self.cat = Category.objects.create(name="Phones", slug="phones")

    def _product(self, slug, **overrides):
        fields = {
            "category": self.cat,
            "shop": self.shop,
            "brand": "Acme",
            "name": slug,
            "slug": slug,
            "price": "10000.00",
            "specs": {},
            "last_confirmed_at": None,
            "is_available": True,
        }
        fields.update(overrides)
        return Product.objects.create(**fields)

    def _slugs(self, query=""):
        response = APIClient().get(f"/api/v1/catalog/products/{query}")
        self.assertEqual(response.status_code, 200)
        return [r["slug"] for r in response.data["results"]]

    def _results(self, query=""):
        response = APIClient().get(f"/api/v1/catalog/products/{query}")
        self.assertEqual(response.status_code, 200)
        return {r["slug"]: r for r in response.data["results"]}

    def test_fresh_outranks_otherwise_identical_stale(self):
        # Created fresh-first on purpose: under the inherited "-created_at"
        # ordering the stale one would come back first, so this fails loudly if
        # the ranking is ever dropped.
        self._product(
            "fresh-one",
            confirmation_status="fresh",
            last_confirmed_at=timezone.now() - timedelta(days=1),
        )
        self._product(
            "stale-one",
            confirmation_status="stale",
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )
        self.assertEqual(self._slugs(), ["fresh-one", "stale-one"])

    def test_unconfirmed_ranks_below_stale(self):
        self._product("never-confirmed")
        self._product(
            "stale-one",
            confirmation_status="stale",
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )
        self._product(
            "fresh-one",
            confirmation_status="fresh",
            last_confirmed_at=timezone.now() - timedelta(days=1),
        )
        self.assertEqual(self._slugs(), ["fresh-one", "stale-one", "never-confirmed"])

    def test_recency_breaks_ties_inside_a_tier(self):
        self._product("older", last_confirmed_at=timezone.now() - timedelta(days=10))
        self._product("newer", last_confirmed_at=timezone.now() - timedelta(days=2))
        self.assertEqual(self._slugs(), ["newer", "older"])

    def test_unavailable_products_stay_excluded(self):
        self._product("in-stock", last_confirmed_at=timezone.now())
        self._product("sold-out", last_confirmed_at=timezone.now(), is_available=False)
        self.assertEqual(self._slugs(), ["in-stock"])

    def test_stale_products_are_included_by_default(self):
        self._product(
            "stale-one",
            confirmation_status="stale",
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )
        self.assertIn("stale-one", self._slugs())
        self.assertNotIn("stale-one", self._slugs("?freshness=1"))

    def test_fresh_filter_uses_confirmation_timestamp(self):
        self._product(
            "recent-confirmation",
            confirmation_status="stale",
            last_confirmed_at=timezone.now() - timedelta(days=1),
        )
        self._product(
            "old-confirmation",
            confirmation_status="fresh",
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )

        self.assertEqual(self._slugs("?fresh=true"), ["recent-confirmation"])

    def test_list_carries_one_trust_signal_and_its_timestamp(self):
        # Replaces test_list_exposes_the_derived_freshness_tier. That test pinned
        # `freshness` into the buyer payload alongside `confirmation_status`,
        # which is how the two came to contradict each other on screen: the same
        # product could serialise as fresh on one field and expired on the other,
        # because they read a 30-day and a 7-day clock respectively.
        #
        # Buyers now get the column the confirmation loop maintains, plus the
        # timestamp, and the client renders age from the timestamp.
        self._product("just-confirmed", last_confirmed_at=timezone.now())
        self._product(
            "long-gone", last_confirmed_at=timezone.now() - timedelta(days=10)
        )

        results = self._results()
        for slug in ("just-confirmed", "long-gone"):
            with self.subTest(slug=slug):
                self.assertIn("confirmation_status", results[slug])
                self.assertIn("last_confirmed_at", results[slug])
                self.assertNotIn("freshness", results[slug])

    def test_unconfirmed_products_are_labelled_not_hidden(self):
        # The whole point of the three-value ladder: a merchant who stopped
        # answering costs the listing its badge, never its visibility.
        self._product("silent", confirmation_status="unconfirmed")
        self._product("answering", last_confirmed_at=timezone.now())

        results = self._results()
        self.assertIn("silent", results)
        self.assertEqual(results["silent"]["confirmation_status"], "unconfirmed")
        self.assertEqual(results["answering"]["confirmation_status"], "fresh")

    def test_detail_carries_the_same_signal_as_the_list(self):
        # List and detail share one serializer, but nothing else pins that down.
        self._product("solo", last_confirmed_at=timezone.now())
        response = APIClient().get("/api/v1/catalog/products/solo/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["confirmation_status"], "fresh")
        self.assertNotIn("freshness", response.data)

    def test_fresh_filter_excludes_never_confirmed(self):
        # The most common row in the catalog today, so worth pinning at the API
        # layer and not only on the queryset helper.
        self._product("confirmed", last_confirmed_at=timezone.now())
        self._product("never-confirmed")
        self.assertEqual(self._slugs("?fresh=true"), ["confirmed"])

    def test_fresh_false_does_not_filter(self):
        # `fresh` is the one flag that reads its value rather than its presence:
        # a buyer asking for fresh=false is asking for the unfiltered list.
        self._product("confirmed", last_confirmed_at=timezone.now())
        self._product(
            "long-gone", last_confirmed_at=timezone.now() - timedelta(days=10)
        )
        self.assertEqual(
            sorted(self._slugs("?fresh=false")), ["confirmed", "long-gone"]
        )

    def test_explicit_ordering_overrides_ranking_without_a_search_term(self):
        # `ordering` used to be honoured only alongside `q`, so this asserts both
        # that it overrides the ranking and that it works on its own.
        self._product(
            "expensive-fresh",
            price="90000.00",
            last_confirmed_at=timezone.now(),
        )
        self._product(
            "cheap-stale",
            price="500.00",
            confirmation_status="stale",
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )
        self.assertEqual(
            self._slugs("?ordering=price"), ["cheap-stale", "expensive-fresh"]
        )

    def test_filters_still_compose_with_ranking(self):
        accessories = Category.objects.create(name="Accessories", slug="accessories")
        self._product(
            "phone-stale",
            confirmation_status="stale",
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )
        self._product("phone-fresh", last_confirmed_at=timezone.now())
        self._product(
            "cable-fresh", category=accessories, last_confirmed_at=timezone.now()
        )
        self.assertEqual(
            self._slugs("?category=phones"), ["phone-fresh", "phone-stale"]
        )
