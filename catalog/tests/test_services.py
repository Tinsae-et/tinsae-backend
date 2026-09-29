"""Direct tests for the catalogue write path.

No Telegram fixture here on purpose. The bot's callback tests in `bot/tests.py`
cover the delivery layer; these pin the domain transitions one level down, so a
future web caller is exercising tested code rather than inheriting the bot's
coverage by accident.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bot.models import ConfirmationRequest
from catalog.models import Category, ConfirmationStatus, Product
from catalog.services import (
    answer_confirmation_request,
    apply_confirmation,
    mark_back_in_stock,
    mark_sold,
    resolve_request,
)
from shops.models import Shop


class ConfirmationServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="merchant", password="asdfg1234"
        )
        # No telegram_chat_id: the domain service must not need one.
        self.shop = Shop.objects.create(name="Bole Electronics", owner=self.user)
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Samsung",
            name="Galaxy S23",
            price="10000.00",
            last_confirmed_at=timezone.now() - timedelta(days=40),
            confirmation_status=ConfirmationStatus.STALE,
            is_available=True,
        )

    def _request(self, status="pending"):
        return ConfirmationRequest.objects.create(
            product=self.product, shop=self.shop, status=status
        )

    # --- apply_confirmation ------------------------------------------------

    def test_confirming_restarts_the_clock_and_flips_the_tier(self):
        apply_confirmation(self.product, confirmed=True)

        self.product.refresh_from_db()
        self.assertGreater(
            self.product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(self.product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertTrue(self.product.is_available)

    def test_selling_out_also_counts_as_fresh_information(self):
        """The both-axes rule: availability and freshness are independent.

        "It's sold" is a merchant confirming reality, so it restarts the clock
        even though it makes the product unavailable. Pinned here directly —
        every other test of this goes through a Telegram callback.
        """
        apply_confirmation(self.product, confirmed=False)

        self.product.refresh_from_db()
        self.assertFalse(self.product.is_available)
        self.assertEqual(self.product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertGreater(
            self.product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )

    def test_it_works_without_a_confirmation_request(self):
        """The /myproducts path answers about products that were never prompted."""
        apply_confirmation(self.product, confirmed=True)

        self.product.refresh_from_db()
        self.assertEqual(self.product.confirmation_status, ConfirmationStatus.FRESH)

    def test_an_outstanding_request_is_resolved_with_the_product(self):
        request = self._request()

        apply_confirmation(self.product, confirmed=True, request=request)

        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertIsNotNone(request.responded_at)

    def test_a_denial_resolves_the_request_as_sold_out(self):
        request = self._request()

        apply_confirmation(self.product, confirmed=False, request=request)

        request.refresh_from_db()
        self.assertEqual(request.status, "sold_out")

    # --- resolve_request ---------------------------------------------------

    def test_resolve_rejects_an_unknown_status(self):
        with self.assertRaises(ValueError):
            resolve_request(self._request(), "bogus")

    def test_expiring_one_request_records_no_response_time(self):
        """A timeout is the absence of an answer, so it leaves responded_at NULL —
        the single-request path must agree with the sweep's bulk expiry."""
        request = self._request()
        resolve_request(request, "expired")
        request.refresh_from_db()
        self.assertEqual(request.status, "expired")
        self.assertIsNone(request.responded_at)

    def test_resolving_twice_keeps_the_first_answer(self):
        request = self._request()
        resolve_request(request, "confirmed")
        first = request.responded_at
        resolve_request(request, "sold_out")
        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertEqual(request.responded_at, first)


class SearchFilterTests(TestCase):
    def test_build_search_filter_caps_terms_at_max_search_terms(self):
        from catalog.services import MAX_SEARCH_TERMS, build_search_filter

        query_words = [f"word{i}" for i in range(15)]
        long_query = " ".join(query_words)
        q_filter = build_search_filter(long_query)

        q_str = str(q_filter)
        for i in range(MAX_SEARCH_TERMS):
            self.assertIn(f"word{i}", q_str)
        for i in range(MAX_SEARCH_TERMS, 15):
            self.assertNotIn(f"word{i}", q_str)


class MarkSoldAndBackInStockTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="merchant", password="password123"
        )
        self.shop = Shop.objects.create(name="Bole Electronics", owner=self.user)
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Samsung",
            name="Galaxy S23",
            price="10000.00",
            last_confirmed_at=timezone.now() - timedelta(days=40),
            confirmation_status=ConfirmationStatus.STALE,
            is_available=True,
        )

    def test_mark_sold_sets_unavailable_and_refreshes_clock(self):
        mark_sold(self.product)

        self.product.refresh_from_db()
        self.assertFalse(self.product.is_available)
        self.assertEqual(self.product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertGreater(
            self.product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )

    def test_mark_back_in_stock_sets_available_and_refreshes_clock(self):
        self.product.is_available = False
        self.product.save(update_fields=["is_available"])

        mark_back_in_stock(self.product)

        self.product.refresh_from_db()
        self.assertTrue(self.product.is_available)
        self.assertEqual(self.product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertGreater(
            self.product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )


class AnswerConfirmationRequestTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="merchant", password="password123"
        )
        self.chat_id = "12345678"
        self.shop = Shop.objects.create(
            name="Bole Electronics",
            owner=self.user,
            telegram_chat_id=self.chat_id,
        )
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Samsung",
            name="Galaxy S23",
            price="10000.00",
            last_confirmed_at=timezone.now() - timedelta(days=40),
            confirmation_status=ConfirmationStatus.STALE,
            is_available=True,
        )
        self.request = ConfirmationRequest.objects.create(
            product=self.product, shop=self.shop, status="pending"
        )

    def test_owning_chat_confirms_available(self):
        outcome, product = answer_confirmation_request(
            chat_id=self.chat_id, request_id=self.request.pk, available=True
        )

        self.assertEqual(outcome, "confirmed")
        self.assertEqual(product.pk, self.product.pk)
        product.refresh_from_db()
        self.assertTrue(product.is_available)
        self.assertEqual(product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertGreater(
            product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "confirmed")
        self.assertIsNotNone(self.request.responded_at)

    def test_owning_chat_marks_sold_out(self):
        outcome, product = answer_confirmation_request(
            chat_id=self.chat_id, request_id=self.request.pk, available=False
        )

        self.assertEqual(outcome, "sold_out")
        self.assertEqual(product.pk, self.product.pk)
        product.refresh_from_db()
        self.assertFalse(product.is_available)
        self.assertEqual(product.confirmation_status, ConfirmationStatus.FRESH)
        self.assertGreater(
            product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "sold_out")
        self.assertIsNotNone(self.request.responded_at)

    def test_foreign_chat_returns_not_found_and_mutates_nothing(self):
        before_confirmed_at = self.product.last_confirmed_at

        outcome, product = answer_confirmation_request(
            chat_id="99999999", request_id=self.request.pk, available=True
        )

        self.assertEqual(outcome, "not_found")
        self.assertIsNone(product)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "pending")
        self.product.refresh_from_db()
        self.assertEqual(self.product.last_confirmed_at, before_confirmed_at)
        self.assertEqual(self.product.confirmation_status, ConfirmationStatus.STALE)

    def test_missing_request_id_returns_not_found(self):
        outcome, product = answer_confirmation_request(
            chat_id=self.chat_id, request_id=99999999, available=True
        )

        self.assertEqual(outcome, "not_found")
        self.assertIsNone(product)

    def test_already_resolved_request_returns_already_answered_and_is_noop(self):
        self.request.status = "confirmed"
        self.request.responded_at = timezone.now() - timedelta(hours=1)
        self.request.save()
        original_responded_at = self.request.responded_at
        before_confirmed_at = self.product.last_confirmed_at

        outcome, product = answer_confirmation_request(
            chat_id=self.chat_id, request_id=self.request.pk, available=False
        )

        self.assertEqual(outcome, "already_answered")
        self.assertEqual(product.pk, self.product.pk)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "confirmed")
        self.assertEqual(self.request.responded_at, original_responded_at)
        self.product.refresh_from_db()
        self.assertEqual(self.product.last_confirmed_at, before_confirmed_at)
        self.assertTrue(self.product.is_available)
