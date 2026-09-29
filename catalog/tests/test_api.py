from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import (
    Category,
    ConfirmationStatus,
    Product,
    ProductStatus,
    SearchLog,
)
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
        self.assertEqual(response.data["results"][0]["condition"], "new")

    def test_product_detail_by_slug(self):
        response = APIClient().get("/api/v1/catalog/products/galaxy-a15/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["specs"], {"ram": "4GB", "storage": "128GB"})
        self.assertEqual(response.data["condition"], "new")

    def test_public_read_exposes_used_and_refurbished_condition(self):
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Apple",
            name="iPhone 13",
            slug="iphone-13-used",
            price="45000.00",
            condition="used",
            last_confirmed_at=timezone.now(),
        )
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Apple",
            name="iPhone 14",
            slug="iphone-14-refurb",
            price="55000.00",
            condition="refurbished",
            last_confirmed_at=timezone.now(),
        )

        detail_used = APIClient().get("/api/v1/catalog/products/iphone-13-used/")
        self.assertEqual(detail_used.status_code, 200)
        self.assertEqual(detail_used.data["condition"], "used")

        detail_refurb = APIClient().get("/api/v1/catalog/products/iphone-14-refurb/")
        self.assertEqual(detail_refurb.status_code, 200)
        self.assertEqual(detail_refurb.data["condition"], "refurbished")

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

    def test_search_keyword_match(self):
        response = APIClient().get("/api/v1/catalog/products/?q=gala")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["title"], "Samsung Galaxy A15")

    def test_filter_by_brand_is_case_insensitive(self):
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Apple",
            name="iPhone 15",
            slug="iphone-15",
            price="90000.00",
            last_confirmed_at=timezone.now(),
        )

        response = APIClient().get("/api/v1/catalog/products/?brand=apple")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Apple iPhone 15")

    def test_brand_filter_accepts_a_comma_separated_list(self):
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Apple",
            name="iPhone 15",
            slug="iphone-15",
            price="90000.00",
            last_confirmed_at=timezone.now(),
        )

        response = APIClient().get(
            "/api/v1/catalog/products/?brand=Samsung,Apple"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

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

    def test_fresh_is_the_buyer_facing_freshness_filter(self):
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

        response = APIClient().get("/api/v1/catalog/products/?fresh=true")
        self.assertEqual(response.status_code, 200)
        titles = [r["title"] for r in response.data["results"]]
        self.assertNotIn("Nokia Stale Phone", titles)
        self.assertEqual(len(response.data["results"]), 2)

    @patch("catalog.views.logger.warning")
    def test_freshness_is_deprecated_and_not_interchangeable_with_fresh(self, warning):
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Nokia",
            name="Stale Phone",
            slug="stale-phone",
            price="5000.00",
            specs={},
            last_confirmed_at=timezone.now() - timedelta(days=60),
            confirmation_status="stale",
        )

        fresh_response = APIClient().get("/api/v1/catalog/products/?fresh=true")
        deprecated_response = APIClient().get(
            "/api/v1/catalog/products/?freshness=true"
        )

        self.assertEqual(fresh_response.status_code, 200)
        self.assertEqual(deprecated_response.status_code, 200)
        self.assertNotEqual(
            {item["slug"] for item in fresh_response.data["results"]},
            {item["slug"] for item in deprecated_response.data["results"]},
        )
        self.assertEqual(deprecated_response.data["count"], 2)
        warning.assert_called_once()
        self.assertIn(
            "Deprecated query parameter ?freshness=", warning.call_args.args[0]
        )

    @patch("catalog.views.logger.warning")
    def test_empty_deprecated_freshness_parameter_is_logged(self, warning):
        response = APIClient().get("/api/v1/catalog/products/?freshness=")

        self.assertEqual(response.status_code, 200)
        warning.assert_called_once()

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
            "/api/v1/catalog/products/?q=gala&min_price=20000&max_price=35000&fresh=true&verified_only=ok&category=phones1"
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
        self.assertNotIn("stale-one", self._slugs("?fresh=true"))

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


class ProductSearchTest(TestCase):
    """Tests for product search and ranking."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="search_merchant",
            password="pass",
        )

        self.verified_shop = Shop.objects.create(
            name="Verified Shop",
            slug="verified-shop",
            owner=self.owner,
            is_verified=True,
        )

        self.unverified_shop = Shop.objects.create(
            name="Unverified Shop",
            slug="unverified-shop",
            owner=self.owner,
            is_verified=False,
        )

        self.phone_category = Category.objects.create(
            name="Phones",
            slug="phones",
        )

        self.laptop_category = Category.objects.create(
            name="Laptops",
            slug="laptops",
        )

    def _product(self, slug, **overrides):
        fields = {
            "category": self.phone_category,
            "shop": self.verified_shop,
            "brand": "Samsung",
            "name": "Samsung Phone",
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

    def test_search_by_product_name(self):
        self._product(
            "samsung-s24",
            name="Samsung Galaxy S24",
        )

        self.assertEqual(
            self._slugs("?q=samsung"),
            ["samsung-s24"],
        )

    def test_search_by_brand(self):
        self._product(
            "samsung-phone",
            brand="Samsung",
        )

        self.assertEqual(
            self._slugs("?q=samsung"),
            ["samsung-phone"],
        )

    def test_search_by_category(self):
        self._product(
            "samsung-s24",
            name="Galaxy S24",
            brand="Samsung",
            category=self.phone_category,
        )

        self.assertIn(
            "samsung-s24",
            self._slugs("?q=phones"),
        )

    def test_search_synonym(self):
        self._product(
            "samsung-phone",
            name="Samsung Galaxy",
            brand="Samsung",
        )

        self.assertIn(
            "samsung-phone",
            self._slugs("?q=phone"),
        )

    def test_search_multiple_words(self):
        self._product(
            "samsung-s24",
            name="Samsung Galaxy S24",
            brand="Samsung",
        )

        self.assertIn(
            "samsung-s24",
            self._slugs("?q=samsung phone"),
        )

    def test_unavailable_products_are_excluded(self):
        self._product(
            "available-phone",
            name="Samsung Phone",
            is_available=True,
        )

        self._product(
            "sold-out-phone",
            name="Samsung Phone",
            is_available=False,
        )

        results = self._slugs("?q=samsung")

        self.assertIn("available-phone", results)
        self.assertNotIn("sold-out-phone", results)

    def test_ranking_internals_are_not_serialised(self):
        # The scores are implementation detail of `apply_search` and move every
        # time the weights are retuned. Rank reaches clients as list order.
        self._product("samsung-phone", name="Samsung Phone")

        product = self._results("?q=samsung")["samsung-phone"]

        for key in ("typo_score", "verified_score", "rank_score", "freshness_score"):
            self.assertNotIn(key, product)

    def test_verified_product_ranks_higher(self):
        self._product(
            "unverified-phone",
            name="Samsung Phone",
            shop=self.unverified_shop,
        )

        self._product(
            "verified-phone",
            name="Samsung Phone",
            shop=self.verified_shop,
        )

        self.assertEqual(
            self._slugs("?q=samsung"),
            [
                "verified-phone",
                "unverified-phone",
            ],
        )

    def test_empty_query_is_an_absent_filter_not_an_empty_result(self):
        # Changed with TIN-58. Search moved onto the catalogue list route, where
        # `q` is one filter among several -- and an absent filter narrows
        # nothing. `?q=` is the buyer clearing the search box, which should show
        # them the catalogue, not a blank page.
        self._product("samsung-phone", name="Samsung Phone")

        self.assertEqual(self._slugs("?q="), ["samsung-phone"])

    def test_extra_words_narrow_rather_than_widen(self):
        # Terms AND. The previous filter ORed them, so every added word grew the
        # result set and the more specific a buyer was the worse they did.
        self._product("samsung-laptop", brand="Samsung", name="Samsung Notebook 14")
        self._product("hp-laptop", brand="HP", name="HP Notebook 15")
        self._product("samsung-phone", brand="Samsung", name="Samsung Galaxy S24")

        self.assertEqual(self._slugs("?q=samsung laptop"), ["samsung-laptop"])

    def test_relevance_outranks_freshness(self):
        # The ordering this replaced sorted on `last_confirmed_at` first, which
        # meant a barely-relevant fresh listing beat an exact match.
        self._product(
            "weak-match-fresh",
            brand="Samsung",
            name="Samsung Galaxy S24",
            description="ships in a box with a free notebook",
            last_confirmed_at=timezone.now(),
        )
        self._product(
            "strong-match-stale",
            brand="Lenovo",
            name="Notebook Pro",
            category=self.laptop_category,
            confirmation_status=ConfirmationStatus.STALE,
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )

        self.assertEqual(self._slugs("?q=notebook")[0], "strong-match-stale")

    def test_never_confirmed_does_not_float_to_the_top(self):
        # Postgres sorts NULLs first on DESC. Ordering on `-last_confirmed_at`
        # therefore put never-confirmed listings above every confirmed one.
        self._product("never-confirmed", name="Samsung Phone", last_confirmed_at=None)
        self._product(
            "confirmed",
            name="Samsung Phone",
            last_confirmed_at=timezone.now() - timedelta(days=40),
        )

        self.assertEqual(self._slugs("?q=samsung")[0], "confirmed")

    def test_search_composes_with_filters(self):
        # The removed /productsearch/ route dropped every filter on the floor.
        self._product("cheap-phone", name="Samsung Phone", price="5000.00")
        self._product("pricey-phone", name="Samsung Phone", price="90000.00")

        self.assertEqual(self._slugs("?q=samsung&max_price=10000"), ["cheap-phone"])

    def test_explicit_ordering_overrides_relevance(self):
        self._product("cheap-phone", name="Samsung Phone", price="5000.00")
        self._product(
            "pricey-phone",
            name="Samsung Phone",
            price="90000.00",
            shop=self.verified_shop,
        )

        self.assertEqual(
            self._slugs("?q=samsung&ordering=price"),
            ["cheap-phone", "pricey-phone"],
        )


class SearchLoggingTest(TestCase):
    """TIN-58 AC4. Nothing else in the ticket is tunable without these rows."""

    def _search(self, query):
        return APIClient().get(f"/api/v1/catalog/products/?q={query}")

    def test_search_is_logged_with_its_result_count(self):
        self._search("samsung")

        entry = SearchLog.objects.get()
        self.assertEqual(entry.raw_query, "samsung")
        self.assertEqual(entry.result_count, 0)
        self.assertIsNone(entry.user)

    def test_query_is_normalised_for_aggregation(self):
        self._search("  Samsung   PHONE ")

        self.assertEqual(SearchLog.objects.get().normalized, "samsung phone")

    def test_browsing_without_a_query_logs_nothing(self):
        APIClient().get("/api/v1/catalog/products/")
        APIClient().get("/api/v1/catalog/products/?q=")

        self.assertEqual(SearchLog.objects.count(), 0)

    def test_logging_failure_does_not_break_search(self):
        with patch(
            "catalog.services.SearchLog.objects.create",
            side_effect=DatabaseError("disk full"),
        ):
            response = self._search("samsung")

        self.assertEqual(response.status_code, 200)


class SearchLogRetentionTest(TestCase):
    """D9's open item. Search terms tied to accounts need a stated expiry."""

    def _log(self, query, age_days):
        entry = SearchLog.objects.create(
            raw_query=query, normalized=query, result_count=0
        )
        # `created_at` is auto_now_add, so it cannot be set on create.
        SearchLog.objects.filter(pk=entry.pk).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
        return entry

    def test_rows_past_retention_are_deleted(self):
        self._log("old", age_days=200)
        self._log("recent", age_days=10)

        call_command("sweep_search_logs", verbosity=0)

        self.assertEqual(
            list(SearchLog.objects.values_list("raw_query", flat=True)), ["recent"]
        )

    def test_dry_run_deletes_nothing(self):
        self._log("old", age_days=200)

        call_command("sweep_search_logs", "--dry-run", verbosity=0)

        self.assertEqual(SearchLog.objects.count(), 1)

    def test_retention_days_can_be_overridden(self):
        self._log("five-days-old", age_days=5)

        call_command("sweep_search_logs", "--retention-days", "1", verbosity=0)

        self.assertEqual(SearchLog.objects.count(), 0)


class SimilarProductsApiTest(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="similar_merchant", password="pass")
        self.shop = Shop.objects.create(
            name="Similar Shop", slug="similar-shop", owner=owner
        )
        self.phone_category = Category.objects.create(
            name="Phones", slug="similar-phones"
        )
        self.laptop_category = Category.objects.create(
            name="Laptops", slug="similar-laptops"
        )
        self.source = self._product("source-phone", brand="Samsung", price="10000.00")

    def _product(self, slug, **overrides):
        fields = {
            "category": self.phone_category,
            "shop": self.shop,
            "brand": "Apple",
            "name": "Test Product",
            "slug": slug,
            "price": "10000.00",
            "last_confirmed_at": timezone.now(),
            "is_available": True,
        }
        fields.update(overrides)
        return Product.objects.create(**fields)

    def _slugs(self):
        response = APIClient().get(f"/api/v1/catalog/similar/{self.source.slug}/")
        self.assertEqual(response.status_code, 200)
        return {item["slug"] for item in response.data["results"]}

    def test_matching_rules_include_category_price_brand_and_combination(self):
        # Within same category and within ±20% price band (11,000 is +10%)
        self._product("same-category-price", brand="Apple", price="11000.00")
        # Within same category and same brand, but outside price band (50,000)
        self._product(
            "same-brand-in-category",
            brand="Samsung",
            price="50000.00",
        )
        # Within same category, same brand, AND within price band
        self._product("same-category-and-brand", brand="Samsung", price="10500.00")

        # Products in a DIFFERENT category must NEVER match, even if same brand or price
        self._product(
            "diff-category-same-brand",
            category=self.laptop_category,
            brand="Samsung",
            price="10000.00",
        )
        self._product(
            "diff-category-same-price",
            category=self.laptop_category,
            brand="Apple",
            price="10000.00",
        )

        slugs = self._slugs()
        self.assertIn("same-category-price", slugs)
        self.assertIn("same-brand-in-category", slugs)
        self.assertIn("same-category-and-brand", slugs)
        self.assertNotIn("diff-category-same-brand", slugs)
        self.assertNotIn("diff-category-same-price", slugs)

    def test_source_product_is_excluded(self):
        self._product("another-phone", price="10500.00")

        self.assertNotIn(self.source.slug, self._slugs())

    def test_expired_products_are_excluded(self):
        self._product(
            "expired-phone",
            price="10500.00",
            last_confirmed_at=timezone.now() - timedelta(days=8),
        )

        self.assertNotIn("expired-phone", self._slugs())

    def test_empty_case_returns_no_similar_products(self):
        self.assertEqual(self._slugs(), set())

    def test_unknown_slug_returns_404(self):
        response = APIClient().get("/api/v1/catalog/similar/non-existent-product/")
        self.assertEqual(response.status_code, 404)

    def test_shop_preference_prioritizes_competitor_shops(self):
        other_owner = User.objects.create_user(
            username="other_merchant", password="pass"
        )
        competitor_shop = Shop.objects.create(
            name="Competitor Shop", slug="competitor-shop", owner=other_owner
        )

        # Same shop product
        self._product("same-shop-item", shop=self.shop, price="10100.00")
        # Competitor shop product
        self._product("competitor-shop-item", shop=competitor_shop, price="10100.00")

        response = APIClient().get(f"/api/v1/catalog/similar/{self.source.slug}/")
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertGreaterEqual(len(results), 2)
        # Competitor product should be ranked ahead of the same-shop product
        self.assertEqual(results[0]["slug"], "competitor-shop-item")
        self.assertEqual(results[1]["slug"], "same-shop-item")


class PublicStatsApiTest(TestCase):
    def setUp(self):
        cache.clear()
        owner = User.objects.create_user(username="stats_merchant", password="pass")
        self.shop = Shop.objects.create(
            name="Stats Shop", slug="stats-shop", owner=owner, is_verified=True
        )
        self.category = Category.objects.create(
            name="Stats Phones", slug="stats-phones"
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _product(self, slug, **overrides):
        fields = {
            "category": self.category,
            "shop": self.shop,
            "brand": "Samsung",
            "name": "Stats Product",
            "slug": slug,
            "price": "12500.00",
            "is_available": True,
            "status": ProductStatus.LIVE,
            "last_confirmed_at": timezone.now(),
        }
        fields.update(overrides)
        return Product.objects.create(**fields)

    def test_counts_exclude_expired_listings(self):
        now = timezone.now()
        # Fresh listing (within 48h): counts as both buyer-visible and fresh
        self._product("fresh-product", last_confirmed_at=now - timedelta(hours=1))
        # Almost expired listing (3 days ago, within 7d): counts as buyer-visible, but NOT fresh
        self._product(
            "almost-expired-product",
            last_confirmed_at=now - timedelta(days=3),
        )
        # Expired listing (8 days ago, past 7d): excluded from buyer-visible and fresh
        self._product(
            "expired-product",
            last_confirmed_at=now - timedelta(days=8),
        )
        # Never confirmed listing (None): excluded
        self._product("never-confirmed-product", last_confirmed_at=None)

        response = APIClient().get("/api/v1/catalog/public-stats/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_listings"], 2)
        self.assertEqual(response.data["fresh_listings"], 1)
        self.assertEqual(response.data["total_products"], 2)
        self.assertEqual(response.data["verified_shop_products"], 2)
        self.assertEqual(response.data["category_product_count"], {"stats-phones": 2})

    def test_counts_exclude_paused_and_draft_listings(self):
        # Live available product
        self._product("live-product", is_available=True, status=ProductStatus.LIVE)
        # Paused / unavailable
        self._product("paused-product", is_available=False, status=ProductStatus.LIVE)
        # Draft status
        self._product("draft-product", is_available=False, status=ProductStatus.DRAFT)
        # Sold status
        self._product("sold-product", is_available=False, status=ProductStatus.SOLD)

        response = APIClient().get("/api/v1/catalog/public-stats/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_listings"], 1)
        self.assertEqual(response.data["fresh_listings"], 1)

    def test_total_shops_and_verified_shops_counts(self):
        # 1 verified shop from setUp, add 1 unverified shop
        unverified_owner = User.objects.create_user(
            username="unverified_merchant", password="pass"
        )
        Shop.objects.create(
            name="Unverified Shop",
            slug="unverified-shop",
            owner=unverified_owner,
            is_verified=False,
        )

        response = APIClient().get("/api/v1/catalog/public-stats/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_shops"], 2)
        self.assertEqual(response.data["verified_shops"], 1)

    def test_response_shape_and_no_identifying_data_exposed(self):
        self._product("stats-product")

        response = APIClient().get("/api/v1/catalog/public-stats/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data),
            {
                "total_shops",
                "total_listings",
                "fresh_listings",
                "total_products",
                "verified_shops",
                "verified_shop_products",
                "category_product_count",
            },
        )
        # AC requirement: No shop-identifying or owner data is exposed
        self.assertNotIn("shop_product_count", response.data)
        serialized_str = str(response.data)
        self.assertNotIn(self.shop.name, serialized_str)
        self.assertNotIn(self.shop.slug, serialized_str)
        self.assertNotIn(self.shop.owner.username, serialized_str)

    def test_second_request_within_ttl_is_served_from_cache(self):
        self._product("initial-stats-product")

        first_response = APIClient().get("/api/v1/catalog/public-stats/")
        self._product("new-stats-product")
        second_response = APIClient().get("/api/v1/catalog/public-stats/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response.data["total_listings"], 1)
        self.assertEqual(second_response.data["total_listings"], 1)
