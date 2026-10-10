from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bot.models import ConfirmationRequest
from catalog.models import Category, ConfirmationStatus, Product
from catalog.services import apply_confirmation
from ops.metrics import collect_health
from shops.models import Shop
from users.models import User


class MetricsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="merchant", password="pw")
        self.category = Category.objects.create(name="Phones", slug="phones")

    def _shop(self, name, **kwargs):
        return Shop.objects.create(name=name, owner=self.owner, **kwargs)

    def _product(self, shop, name, *, days_ago=1, available=True, status="fresh"):
        return Product.objects.create(
            shop=shop,
            category=self.category,
            brand="B",
            name=name,
            price="1.00",
            is_available=available,
            confirmation_status=status,
            last_confirmed_at=timezone.now() - timedelta(days=days_ago),
        )

    def _answer(self, shop, product, *, confirmed):
        request = ConfirmationRequest.objects.create(
            product=product, shop=shop, status="pending"
        )
        apply_confirmation(product, confirmed=confirmed, request=request)

    def test_fresh_percentage_covers_available_products_only(self):
        shop = self._shop("S", telegram_chat_id="1")
        self._product(shop, "a", status=ConfirmationStatus.FRESH)
        self._product(shop, "b", status=ConfirmationStatus.FRESH)
        self._product(shop, "c", status=ConfirmationStatus.STALE)
        # Unavailable products are outside the loop and must not move the ratio.
        self._product(shop, "d", available=False, status=ConfirmationStatus.STALE)

        catalog = collect_health()["catalog"]
        self.assertEqual(catalog["available"], 3)
        self.assertEqual(catalog["fresh"], 2)
        self.assertEqual(catalog["stale"], 1)
        self.assertEqual(catalog["fresh_pct"], 66.7)
        self.assertEqual(catalog["unavailable"], 1)

    def test_unconfirmed_products_are_counted_and_do_not_read_as_drift(self):
        """UNCONFIRMED is past the clock too, so drift measures against the sum.

        Counting drift against `stale` alone would report a sweep that is doing
        its job exactly right as broken, which is the one number an operator
        uses to decide whether to go looking.
        """
        shop = self._shop("S", telegram_chat_id="1")
        self._product(shop, "silent", days_ago=45, status="unconfirmed")
        self._product(shop, "due", days_ago=45, status=ConfirmationStatus.STALE)

        catalog = collect_health()["catalog"]
        self.assertEqual(catalog["unconfirmed"], 1)
        self.assertEqual(catalog["stale"], 1)
        self.assertEqual(catalog["stale_by_clock"], 2)
        self.assertEqual(catalog["sweep_drift"], 0)
        self.assertTrue(catalog["sweep_current"])

    def test_sweep_drift_detects_an_unrun_sweep(self):
        shop = self._shop("S")
        # Past the 30-day window but still stored as fresh: exactly what a
        # catalog looks like when nobody scheduled the sweep.
        self._product(shop, "a", days_ago=45, status=ConfirmationStatus.FRESH)

        catalog = collect_health()["catalog"]
        self.assertEqual(catalog["stale"], 0)
        self.assertEqual(catalog["stale_by_clock"], 1)
        self.assertEqual(catalog["sweep_drift"], 1)
        self.assertFalse(catalog["sweep_current"])

        call_command("sweep_freshness")

        catalog = collect_health()["catalog"]
        self.assertEqual(catalog["stale"], 1)
        self.assertEqual(catalog["sweep_drift"], 0)
        self.assertTrue(catalog["sweep_current"])

    def test_response_rate_excludes_pending_and_distinguishes_never_asked(self):
        answering = self._shop("Answering", telegram_chat_id="1")
        silent = self._shop("Silent", telegram_chat_id="2")
        self._shop("Unasked", telegram_chat_id="3")
        self._shop("NoChat")

        p1 = self._product(answering, "p1")
        p2 = self._product(silent, "p2")
        for shop, product, statuses in [
            (answering, p1, ["confirmed", "sold_out", "expired", "expired"]),
            (silent, p2, ["expired", "expired"]),
        ]:
            for status in statuses:
                if status in {"confirmed", "sold_out"}:
                    self._answer(shop, product, confirmed=status == "confirmed")
                else:
                    ConfirmationRequest.objects.create(
                        product=product, shop=shop, status=status
                    )
        # Pending must not drag the rate down -- it is not yet a failure.
        ConfirmationRequest.objects.create(product=p1, shop=answering, status="pending")

        health = collect_health()
        rates = {s["name"]: s["response_rate"] for s in health["shops"]}
        self.assertEqual(rates["Answering"], 50.0)  # 2 answered / 4 resolved
        self.assertEqual(rates["Silent"], 0.0)
        self.assertIsNone(rates["Unasked"])  # never asked, not 0%
        self.assertIsNone(rates["NoChat"])

        attention = health["attention"]
        self.assertEqual([s["name"] for s in attention["never_responds"]], ["Silent"])
        self.assertEqual([s["name"] for s in attention["unreachable"]], ["NoChat"])
        self.assertEqual([s["name"] for s in attention["never_asked"]], ["Unasked"])

        self.assertEqual(health["requests"]["pending"], 1)
        self.assertEqual(health["requests"]["answered"], 2)
        self.assertEqual(health["requests"]["expired"], 4)
        self.assertEqual(health["requests"]["response_rate"], 33.3)

    def test_a_shop_answering_in_the_dashboard_is_not_flagged_unresponsive(self):
        """`expired` is the denominator of the response rate.

        A prompt the merchant answered elsewhere must not lapse: two lapses put
        a shop in `never_responds`, and the remedy that list feeds is
        unverification. This shop replied to everything it was asked.
        """
        shop = self._shop("Replies", telegram_chat_id="1")
        for name in ("a", "b"):
            product = self._product(shop, name, days_ago=45)
            ConfirmationRequest.objects.create(
                product=product, shop=shop, status="pending"
            )
            apply_confirmation(product, confirmed=True)

        health = collect_health()
        self.assertEqual(health["requests"]["answered"], 2)
        self.assertEqual(health["requests"]["expired"], 0)
        self.assertEqual(health["requests"]["response_rate"], 100.0)
        self.assertEqual(health["attention"]["never_responds"], [])

    def test_a_single_lapse_is_not_enough_to_be_flagged(self):
        shop = self._shop("OneMiss", telegram_chat_id="1")
        product = self._product(shop, "p")
        ConfirmationRequest.objects.create(product=product, shop=shop, status="expired")

        attention = collect_health()["attention"]
        self.assertEqual(attention["never_responds"], [])

    def test_per_shop_counts_are_not_inflated_by_the_second_join(self):
        shop = self._shop("S", telegram_chat_id="1")
        p1 = self._product(shop, "p1")
        self._product(shop, "p2")
        self._product(shop, "p3")
        for status in ["confirmed", "expired", "sold_out"]:
            if status in {"confirmed", "sold_out"}:
                self._answer(shop, p1, confirmed=status == "confirmed")
            else:
                ConfirmationRequest.objects.create(product=p1, shop=shop, status=status)

        row = collect_health()["shops"][0]
        self.assertEqual(row["available_products"], 2)  # not 2 x 3
        self.assertEqual(row["answered"], 2)  # not 2 x 2
        self.assertEqual(row["expired"], 1)

    def test_pending_overdue_counts_only_requests_past_their_deadline(self):
        shop = self._shop("S", telegram_chat_id="1")
        # Two products: ConfirmationRequest allows only one pending row per
        # product, so both prompts cannot hang off the same one.
        recent = self._product(shop, "recent")
        old = self._product(shop, "old")
        ConfirmationRequest.objects.create(product=recent, shop=shop, status="pending")
        old_request = ConfirmationRequest.objects.create(
            product=old, shop=shop, status="pending"
        )
        # A request is overdue once its own expires_at has passed.
        ConfirmationRequest.objects.filter(pk=old_request.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )

        requests = collect_health()["requests"]
        self.assertEqual(requests["pending"], 2)
        self.assertEqual(requests["pending_overdue"], 1)
        # Neither is resolved, so the rate is still undefined rather than 0%.
        self.assertIsNone(requests["response_rate"])


class SeedReconciliationTests(TestCase):
    """The AC's "numbers reconcile with the seeded data" check, pinned."""

    def test_seeded_catalog_before_and_after_a_sweep(self):
        # Sample mode creates the categories it needs (every vertical).
        call_command("seed_shop_data", sample=True, no_images=True)

        # Before the sweep the stored column is untouched: the seeder writes
        # last_confirmed_at but never confirmation_status. A health page that
        # read the clock directly would disagree with what buyers actually get.
        before = collect_health()["catalog"]
        self.assertEqual(before["available"], 65)
        self.assertEqual(before["stale"], 0)
        self.assertEqual(before["stale_by_clock"], 5)
        self.assertEqual(before["sweep_drift"], 5)
        self.assertFalse(before["sweep_current"])

        call_command("sweep_freshness")

        after = collect_health()
        # The five samples seeded at 45 days, past the window.
        self.assertEqual(after["catalog"]["stale"], 5)
        self.assertEqual(after["catalog"]["fresh"], 60)
        self.assertEqual(after["catalog"]["fresh_pct"], 92.3)
        self.assertEqual(after["catalog"]["never_confirmed"], 0)
        self.assertTrue(after["catalog"]["sweep_current"])

        # Seeded shops carry no telegram_chat_id, so nothing was ever asked.
        self.assertEqual(after["requests"]["total"], 0)
        self.assertIsNone(after["requests"]["response_rate"])
        self.assertEqual(len(after["shops"]), 13)
        self.assertEqual(len(after["attention"]["unreachable"]), 13)
        self.assertEqual(after["attention"]["never_responds"], [])
        self.assertEqual(after["attention"]["never_asked"], [])


class AccessTests(TestCase):
    def setUp(self):
        User.objects.create_user(username="plain", password="pw")
        User.objects.create_superuser(username="boss", password="pw")

    def test_endpoint_is_staff_only(self):
        url = reverse("ops-health")
        self.assertIn(self.client.get(url).status_code, (401, 403))

        self.client.login(username="plain", password="pw")
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.login(username="boss", password="pw")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("catalog", response.json())

    def test_page_is_staff_only(self):
        self.assertEqual(self.client.get("/ops/health/").status_code, 302)

        self.client.login(username="plain", password="pw")
        self.assertEqual(self.client.get("/ops/health/").status_code, 302)

        self.client.login(username="boss", password="pw")
        self.assertEqual(self.client.get("/ops/health/").status_code, 200)
