"""Regression tests for the findings in `tinsae-architecture-security-review.md`.

Every test in the first seven classes asserts the expected, secured behaviour and
serves as a regression guard now that fixes have landed.

`ExpectedDrift` at the bottom pins behaviour where implementation decisions differ
from earlier planning docs, such as ranking expired products last rather than
completely hiding them.

Run:
    docker compose exec backend python manage.py test tests.test_review_regressions -v 2
"""

from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db.utils import OperationalError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from bot.models import ConfirmationRequest
from catalog.freshness import Freshness
from catalog.models import Category, ConfirmationStatus, Product
from catalog.services import apply_confirmation
from ops.metrics import ANSWERED_STATUSES, collect_health
from shops.models import Shop
from users.models import User

REPO_ROOT = Path(__file__).resolve().parent.parent


class ShopFixture(TestCase):
    """Two merchants, two shops, one product belonging to the first."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.intruder = User.objects.create_user(username="intruder", password="pw")
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

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client


class TelegramChatIdIsNotMerchantWritable(ShopFixture):
    """Finding: HIGH — `telegram_chat_id` is missing from `read_only_fields`.

    `ShopSerializer` is the write shape for update and partial_update
    (`ShopViewSet.get_serializer_class`), and it lists `telegram_chat_id` in
    `fields` but not in `read_only_fields`. The column is the bot's routing
    address for a merchant and is not unique, so a merchant can write any value
    into it — including one that already belongs to somebody else.
    """

    def test_a_merchant_cannot_set_their_own_chat_id_over_the_api(self):
        response = self.client_for(self.owner).patch(
            f"/api/v1/shops/{self.shop.slug}/",
            {"telegram_chat_id": "999999"},
            format="json",
        )
        self.assertIn(response.status_code, (200, 400))

        self.shop.refresh_from_db()
        self.assertEqual(
            self.shop.telegram_chat_id,
            "",
            "telegram_chat_id was written through the public API. Binding a chat "
            "to a shop is what ShopLinkCode exists for — a one-time, "
            "short-lived, row-locked credential. A PATCH is none of those things.",
        )

    def test_a_merchant_cannot_claim_a_chat_id_that_belongs_to_another_shop(self):
        """The part that makes this a security finding and not a tidiness one."""
        self.shop.telegram_chat_id = "12345"
        self.shop.save(update_fields=["telegram_chat_id"])

        self.client_for(self.intruder).patch(
            f"/api/v1/shops/{self.other_shop.slug}/",
            {"telegram_chat_id": "12345"},
            format="json",
        )

        self.other_shop.refresh_from_db()
        self.assertNotEqual(
            self.other_shop.telegram_chat_id,
            "12345",
            "A second shop now claims another merchant's chat id. "
            "`bot.selectors.shops_for_chat` returns every shop matching a chat id, "
            "so the victim's Telegram session can now reach the intruder's "
            "catalogue.",
        )


class PauseAndResumeDoNotLaunderFreshness(ShopFixture):
    """Finding: correctness — `resume_listing` does what `pause_listing` forbids.

    `pause_listing`'s docstring states the rule outright: "Restarting the clock
    here would let a merchant launder a stale listing fresh by pausing and
    resuming it." Three lines below, `resume_listing` calls
    `apply_confirmation(product, confirmed=True)`, which restarts the clock.
    """

    def setUp(self):
        super().setUp()
        self.stale_at = timezone.now() - timedelta(days=20)
        Product.objects.filter(pk=self.product.pk).update(
            last_confirmed_at=self.stale_at,
            confirmation_status=ConfirmationStatus.STALE,
        )
        self.product.refresh_from_db()
        self.url = f"/api/v1/shops/{self.shop.slug}/products/{self.product.slug}/"

    def test_pausing_leaves_the_clock_alone(self):
        """The half that already works. Here so a fix cannot regress it."""
        response = self.client_for(self.owner).post(f"{self.url}pause/")
        self.assertEqual(response.status_code, 200)

        self.product.refresh_from_db()
        self.assertFalse(self.product.is_available)
        self.assertEqual(self.product.last_confirmed_at, self.stale_at)

    def test_resuming_a_paused_listing_does_not_restart_the_clock(self):
        client = self.client_for(self.owner)
        client.post(f"{self.url}pause/")
        client.post(f"{self.url}resume/")

        self.product.refresh_from_db()
        self.assertTrue(self.product.is_available)
        self.assertEqual(
            self.product.last_confirmed_at,
            self.stale_at,
            "Pause then resume reset last_confirmed_at. That is exactly the "
            "laundering pause_listing's docstring says must not be possible: a "
            "merchant restores freshness on a ten-day-old listing with two "
            "clicks and no stock claim.",
        )
        self.assertEqual(self.product.freshness, Freshness.EXPIRED)


class OpsMetricsReadTheStatusTheSystemActuallyWrites(ShopFixture):
    """Finding: correctness — `ops/metrics.py` counts a status that no longer exists.

    `bot/migrations/0004_confirmation_response_tracking` renamed "denied" to
    "sold_out". `ANSWERED_STATUSES` and the `denied=Count(...)` aggregate in
    `_request_health` were not updated, so every merchant who answers "sold out"
    is recorded as not having answered. `ops/tests.py` builds its fixtures with
    `status="denied"` typed by hand, which is why the suite never caught it.
    """

    def _answer_sold_out(self):
        """Go through the real write path, not a hand-typed status string."""
        request = ConfirmationRequest.objects.create(
            product=self.product, shop=self.shop, status="pending"
        )
        apply_confirmation(self.product, confirmed=False, request=request)
        request.refresh_from_db()
        return request

    def test_the_statuses_metrics_counts_are_statuses_the_model_allows(self):
        """The root cause, in one assertion."""
        valid = set(dict(ConfirmationRequest.STATUS_CHOICES))
        self.assertLessEqual(
            set(ANSWERED_STATUSES),
            valid,
            "ops.metrics.ANSWERED_STATUSES names a status ConfirmationRequest "
            f"cannot hold. Model allows {sorted(valid)}.",
        )

    def test_a_sold_out_answer_counts_as_an_answer(self):
        self.shop.telegram_chat_id = "1"
        self.shop.save(update_fields=["telegram_chat_id"])
        request = self._answer_sold_out()
        self.assertEqual(request.status, "sold_out")

        requests = collect_health()["requests"]
        self.assertEqual(
            requests["answered"],
            1,
            "A merchant answered 'sold out' and the response rate recorded "
            "nothing. Availability and freshness are separate axes — 'out of "
            "stock' is an answer, and apply_confirmation restarts the clock on it.",
        )
        self.assertEqual(requests["response_rate"], 100.0)

    def test_a_shop_that_answers_sold_out_is_not_flagged_unresponsive(self):
        """The consequence: the remedy this list feeds is unverification."""
        self.shop.telegram_chat_id = "1"
        self.shop.save(update_fields=["telegram_chat_id"])
        self._answer_sold_out()
        for _ in range(2):
            ConfirmationRequest.objects.create(
                product=self.product, shop=self.shop, status="expired"
            )

        row = next(r for r in collect_health()["shops"] if r["slug"] == self.shop.slug)
        self.assertEqual(row["answered"], 1)

        flagged = [s["slug"] for s in collect_health()["attention"]["never_responds"]]
        self.assertNotIn(
            self.shop.slug,
            flagged,
            "A shop that did answer is queued for unverification as a "
            "non-responder.",
        )


class PriceFiltersRejectNonNumericInput(ShopFixture):
    """Finding: input validation — `min_price` / `max_price` go straight to the ORM.

    `catalog/views.py` passes the raw query param into `price__gte` / `price__lte`.
    A non-numeric value raises django.core.exceptions.ValidationError out of the
    field's `to_python`, which DRF's exception handler does not translate, so the
    request 500s instead of 400s. Every other filter on this endpoint is either
    allowlisted or coerced.
    """

    URL = "/api/v1/catalog/products/"

    def _get(self, **params):
        try:
            return APIClient().get(self.URL, params)
        except Exception as exc:  # noqa: BLE001 - the failure under test
            self.fail(
                f"GET {self.URL} with {params} raised "
                f"{type(exc).__name__}: {exc}. An unparseable filter is a client "
                "error, not a server error — it should be a 400."
            )

    def test_a_non_numeric_min_price_is_a_client_error(self):
        self.assertEqual(self._get(min_price="abc").status_code, 400)

    def test_a_non_numeric_max_price_is_a_client_error(self):
        self.assertEqual(self._get(max_price="abc").status_code, 400)

    def test_values_that_parse_but_cannot_be_compared_are_rejected(self):
        """The half a plain `Decimal(raw)` guard would miss.

        Each of these is a valid Decimal literal, so it survives the parse and
        fails further in: NaN matches no row and returns a silent empty page,
        and an exponent past Postgres's numeric format raises out of the driver.
        """
        for value in ("nan", "-nan", "Infinity", "-Infinity", "1e999999"):
            with self.subTest(value=value):
                self.assertEqual(self._get(min_price=value).status_code, 400)
                self.assertEqual(self._get(max_price=value).status_code, 400)

    def test_a_valid_price_filter_still_works(self):
        """So the fix narrows the input and nothing else."""
        response = self._get(min_price="1")
        self.assertEqual(response.status_code, 200)

    def test_a_decimal_price_filter_still_works(self):
        """Prices are DecimalField -- the filter has to accept the same shape."""
        response = self._get(min_price="1.50", max_price="99999.99")
        self.assertEqual(response.status_code, 200)


class HealthEndpointFailsLoudWhenTheDatabaseIsDown(TestCase):
    """Finding: error handling — the liveness check swallows the failure.

    `health/views.py` catches every exception from the connection test and then
    returns HTTP 200 with `"database": "error"` in the body. Any load balancer,
    uptime monitor, or container orchestrator reading the status code sees a
    healthy instance and keeps routing traffic to it.
    """

    def test_an_unreachable_database_is_not_a_200(self):
        with mock.patch("health.views.connection") as connection:
            connection.cursor.side_effect = OperationalError("connection refused")
            response = APIClient().get("/api/health/")

        self.assertEqual(
            response.status_code,
            503,
            "The database was unreachable and the health check still returned "
            f"{response.status_code}. UptimeRobot and Railway both read the "
            "status code, not the body.",
        )

    def test_a_healthy_database_is_still_a_200(self):
        response = APIClient().get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["database"], "ok")


class ThrottlingIsProxyAware(TestCase):
    """Finding: MEDIUM — `NUM_PROXIES` is unset.

    `LoginIPThrottle` keys on DRF's `get_ident()`. With `NUM_PROXIES` unset, DRF
    reads X-Forwarded-For and trusts the whole chain, so a caller who sends their
    own header controls their throttle key and the per-IP login limit is
    bypassable. Setting it to the real number of proxies makes DRF count back
    from the right end of the chain instead.
    """

    def test_num_proxies_is_configured(self):
        self.assertIn(
            "NUM_PROXIES",
            settings.REST_FRAMEWORK,
            "REST_FRAMEWORK has no NUM_PROXIES. Set it to the number of proxies "
            "in front of the app (0 when there are none) so login throttling "
            "cannot be defeated with a spoofed X-Forwarded-For.",
        )


class ProductionImageRunsProductionSettings(TestCase):
    """Finding: HIGH — nothing pins the settings module in the prod image.

    `manage.py`, `config/wsgi.py`, `config/asgi.py` and `conftest.py` all
    `setdefault` to `config.settings.dev`. The Dockerfile's prod stage sets no
    DJANGO_SETTINGS_MODULE, so gunicorn boots dev settings — DEBUG on, dev
    ALLOWED_HOSTS, dev security middleware — unless the platform happens to
    supply the variable. This is a file-content assertion because the failure is
    in the image, not in the code.
    """

    def test_the_prod_stage_pins_the_settings_module(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        self.assertIn(
            "DJANGO_SETTINGS_MODULE=config.settings.prod",
            dockerfile,
            "The prod stage does not set DJANGO_SETTINGS_MODULE, and every entry "
            "point defaults to config.settings.dev.",
        )


class ExpectedDrift(ShopFixture):
    """Pins behaviour where implementation decisions differ from earlier planning docs.

    The first two tests pin the settled decision that expired products are ranked
    last rather than 404'd/hidden in v1. The third test documents the current state
    of paused vs. sold-out availability representation.
    """

    def test_expired_products_are_still_shown_to_buyers(self):
        """Settled: expired listings are demoted, not hidden.

        `ProductViewSet.get_queryset` filters on `is_available=True` only, and
        `freshness_rank` sorts past-the-clock listings last. Ranking down and
        hiding are different products, and v1 chose ranking down. Pinned here so
        that later hiding them is a deliberate decision rather than a silent one.
        """
        Product.objects.filter(pk=self.product.pk).update(
            last_confirmed_at=timezone.now() - timedelta(days=30),
            confirmation_status=ConfirmationStatus.STALE,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.freshness, Freshness.EXPIRED)

        response = APIClient().get("/api/v1/catalog/products/")
        slugs = [p["slug"] for p in response.data["results"]]
        self.assertIn(self.product.slug, slugs)

    def test_a_never_confirmed_product_is_expired_but_visible(self):
        """The same decision at its widest: never confirmed at all, still listed."""
        never = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Acme",
            name="Never Confirmed",
            price="500.00",
        )
        Product.objects.filter(pk=never.pk).update(last_confirmed_at=None)
        never.refresh_from_db()
        self.assertEqual(never.freshness, Freshness.EXPIRED)

        response = APIClient().get("/api/v1/catalog/products/")
        slugs = [p["slug"] for p in response.data["results"]]
        self.assertIn(never.slug, slugs)

    def test_pause_and_sold_out_are_indistinguishable_in_the_data(self):
        """Open decision #3, demonstrated.

        Both land as `is_available=False` and differ only in whether the clock
        moved. The dashboard cannot render "paused" differently from "sold out",
        and no query can separate them, because the distinction was never stored.
        """
        sold = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Acme",
            name="Sold",
            price="10.00",
        )
        paused = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Acme",
            name="Paused",
            price="10.00",
        )
        url = f"/api/v1/shops/{self.shop.slug}/products/"
        client = self.client_for(self.owner)
        client.post(f"{url}{sold.slug}/mark-sold/")
        client.post(f"{url}{paused.slug}/pause/")

        sold.refresh_from_db()
        paused.refresh_from_db()
        self.assertFalse(sold.is_available)
        self.assertFalse(paused.is_available)
        self.assertEqual(
            sold.is_available,
            paused.is_available,
            "If this ever fails, an availability enum landed and this test "
            "should be deleted.",
        )
