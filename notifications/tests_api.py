"""Tests for notifications API endpoints: list, unread count, mark one, mark all."""

import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.test import APIClient

from catalog.models import Category, Product
from inquiries.models import Inquiry
from notifications.kinds import KINDS, TELEGRAM, Kind
from notifications.models import Notification
from notifications.serializers import NotificationSerializer
from notifications.views import (
    NotificationListView,
    NotificationMarkAllReadView,
    NotificationMarkReadView,
    NotificationUnreadCountView,
)
from shops.models import Shop

User = get_user_model()


class NotificationApiTestCase(TestCase):
    def setUp(self):
        # Throttle counters persist across tests in the cache; clear before each test
        cache.clear()
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            username="bob",
            email="bob@example.com",
            password="password123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_notification(
        self,
        recipient=None,
        kind="inquiry_message",
        payload=None,
        link="/shop/dashboard/inquiries/1",
        read=False,
        created_at=None,
        **kwargs,
    ) -> Notification:
        target = recipient or self.user
        now_ts = timezone.now().timestamp()
        count = Notification.objects.count()
        dedupe_key = f"{kind}:{target.pk}:{count}:{now_ts}"
        notif = Notification.objects.create(
            recipient=target,
            kind=kind,
            payload=payload if payload is not None else {"inquiry_id": 1},
            link=link,
            dedupe_key=dedupe_key,
            read_at=timezone.now() if read else None,
            **kwargs,
        )
        if created_at is not None:
            Notification.objects.filter(pk=notif.pk).update(created_at=created_at)
            notif.refresh_from_db()
        return notif


class NotificationListViewTests(NotificationApiTestCase):
    def test_list_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_paginated_shape_and_page_size(self):
        for i in range(25):
            self._create_notification(payload={"message_id": i, "inquiry_id": i})

        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn("count", data)
        self.assertIn("next", data)
        self.assertIn("previous", data)
        self.assertIn("results", data)
        self.assertEqual(data["count"], 25)
        self.assertEqual(len(data["results"]), 20)  # Default page size
        self.assertIsNotNone(data["next"])

        # Custom page_size query param
        paged_resp = self.client.get("/api/v1/notifications/?page_size=5")
        self.assertEqual(paged_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(paged_resp.data["results"]), 5)

        # page_size capped at max_page_size (100)
        capped_resp = self.client.get("/api/v1/notifications/?page_size=500")
        self.assertEqual(capped_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(capped_resp.data["results"]), 25)

    def test_list_ordered_newest_first_ties_broken_by_id(self):
        now = timezone.now()
        t1 = now - timedelta(hours=2)
        t2 = now - timedelta(hours=1)
        t3 = now

        n1 = self._create_notification(created_at=t1)
        # n2 and n3 have the exact same created_at; -id breaks the tie
        n2 = self._create_notification(created_at=t2)
        n3 = self._create_notification(created_at=t2)
        n4 = self._create_notification(created_at=t3)

        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        # Expected order: n4 (newest), then n3 (same time as n2 but higher id), n2, n1
        self.assertEqual(result_ids, [n4.pk, n3.pk, n2.pk, n1.pk])

    def test_list_only_returns_authenticated_users_notifications(self):
        my_notif = self._create_notification(recipient=self.user)
        self._create_notification(recipient=self.other_user)

        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], my_notif.pk)

    def test_list_excludes_unknown_kinds(self):
        # Even if a deprecated or deleted kind row exists in DB, it must not be listed
        known = self._create_notification(kind="inquiry_reply")
        self._create_notification(kind="legacy_unknown_kind")

        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], known.pk)

    def test_list_renders_each_of_the_four_kinds_correctly(self):
        # 1. inquiry_message (with product, without product, missing keys, truncated body)
        n_msg_prod = self._create_notification(
            kind="inquiry_message",
            payload={"product_name": "Leather Shoes", "body": "Are these available?"},
            link="/shop/dashboard/inquiries/10",
        )
        n_msg_bare = self._create_notification(
            kind="inquiry_message",
            payload={"body": "Hello there"},
            link="/shop/dashboard/inquiries/11",
        )
        n_msg_empty = self._create_notification(
            kind="inquiry_message",
            payload={},
            link="/shop/dashboard/inquiries/12",
        )
        n_msg_long = self._create_notification(
            kind="inquiry_message",
            payload={"body": "x" * 200},
            link="/shop/dashboard/inquiries/13",
        )

        # 2. inquiry_reply (with shop, without shop, truncated body)
        n_reply_shop = self._create_notification(
            kind="inquiry_reply",
            payload={
                "shop_name": "Bole Electronics",
                "body": "Yes, it is in stock.",
            },
            link="/inquiries/20",
        )
        n_reply_bare = self._create_notification(
            kind="inquiry_reply",
            payload={"body": "In stock."},
            link="/inquiries/21",
        )
        n_reply_long = self._create_notification(
            kind="inquiry_reply",
            payload={"shop_name": "Store", "body": "y" * 160},
            link="/inquiries/22",
        )

        # 3. inquiry_unanswered (with product, without product)
        n_unans_prod = self._create_notification(
            kind="inquiry_unanswered",
            payload={"product_name": "Running Shoes"},
            link="/shop/dashboard/inquiries/30",
        )
        n_unans_bare = self._create_notification(
            kind="inquiry_unanswered",
            payload={},
            link="/shop/dashboard/inquiries/31",
        )

        # 4. verification_reviewed (approved vs rejected)
        n_verif_app = self._create_notification(
            kind="verification_reviewed",
            payload={"state": "approved"},
            link="/shop/dashboard/get-verified",
        )
        n_verif_rej = self._create_notification(
            kind="verification_reviewed",
            payload={"state": "rejected"},
            link="/shop/dashboard/get-verified",
        )

        response = self.client.get("/api/v1/notifications/?page_size=100")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items_by_id = {item["id"]: item for item in response.data["results"]}

        # Check inquiry_message with product
        r = items_by_id[n_msg_prod.pk]
        self.assertEqual(r["kind"], "inquiry_message")
        self.assertEqual(r["type"], "message")
        self.assertTrue(r["unread"])
        self.assertEqual(r["title"], "New message about Leather Shoes")
        self.assertEqual(r["body"], "Are these available?")
        self.assertEqual(
            r["cta"], {"label": "Reply", "href": "/shop/dashboard/inquiries/10"}
        )

        # Check inquiry_message without product
        r = items_by_id[n_msg_bare.pk]
        self.assertEqual(r["title"], "New message from a buyer")
        self.assertEqual(r["body"], "Hello there")

        # Check inquiry_message missing payload keys
        r = items_by_id[n_msg_empty.pk]
        self.assertEqual(r["title"], "New message from a buyer")
        self.assertEqual(r["body"], "")

        # Check inquiry_message body truncation
        r = items_by_id[n_msg_long.pk]
        self.assertEqual(r["body"], ("x" * 140) + "…")

        # Check inquiry_reply with shop
        r = items_by_id[n_reply_shop.pk]
        self.assertEqual(r["kind"], "inquiry_reply")
        self.assertEqual(r["type"], "reply")
        self.assertEqual(r["title"], "Bole Electronics replied")
        self.assertEqual(r["body"], "Yes, it is in stock.")
        self.assertEqual(r["cta"], {"label": "Open chat", "href": "/inquiries/20"})

        # Check inquiry_reply without shop
        r = items_by_id[n_reply_bare.pk]
        self.assertEqual(r["title"], "The shop replied")
        self.assertEqual(r["body"], "In stock.")

        # Check inquiry_reply body truncation
        r = items_by_id[n_reply_long.pk]
        self.assertEqual(r["body"], ("y" * 140) + "…")

        # Check inquiry_unanswered with product
        r = items_by_id[n_unans_prod.pk]
        self.assertEqual(r["kind"], "inquiry_unanswered")
        self.assertEqual(r["type"], "message")
        self.assertEqual(r["title"], "A buyer is waiting for your reply")
        self.assertEqual(r["body"], "About Running Shoes")
        self.assertEqual(
            r["cta"], {"label": "Reply", "href": "/shop/dashboard/inquiries/30"}
        )

        # Check inquiry_unanswered without product
        r = items_by_id[n_unans_bare.pk]
        self.assertEqual(r["title"], "A buyer is waiting for your reply")
        self.assertEqual(r["body"], "")

        # Check verification_reviewed approved
        r = items_by_id[n_verif_app.pk]
        self.assertEqual(r["kind"], "verification_reviewed")
        self.assertEqual(r["type"], "verify")
        self.assertEqual(r["title"], "Your shop is verified")
        self.assertEqual(r["body"], "Buyers now see your verified badge.")
        self.assertEqual(
            r["cta"], {"label": "View", "href": "/shop/dashboard/get-verified"}
        )

        # Check verification_reviewed rejected
        r = items_by_id[n_verif_rej.pk]
        self.assertEqual(r["title"], "Verification needs changes")
        self.assertEqual(r["body"], "Review the feedback and resubmit.")
        self.assertEqual(
            r["cta"], {"label": "View", "href": "/shop/dashboard/get-verified"}
        )

    def test_list_query_count_is_constant_for_one_vs_five_items(self):
        # Create 1 notification
        self._create_notification(payload={"body": "First"})

        # Query count with 1 notification: 1 COUNT(*) query + 1 SELECT query
        with self.assertNumQueries(2):
            resp1 = self.client.get("/api/v1/notifications/")
            self.assertEqual(resp1.status_code, status.HTTP_200_OK)

        # Add 4 more notifications (total 5)
        for i in range(4):
            self._create_notification(payload={"body": f"Item {i}"})

        # Query count remains exactly 2 (constant bound, no N+1 for item serialization)
        with self.assertNumQueries(2):
            resp2 = self.client.get("/api/v1/notifications/")
            self.assertEqual(resp2.status_code, status.HTTP_200_OK)
            self.assertEqual(len(resp2.data["results"]), 5)


class NotificationUnreadCountViewTests(NotificationApiTestCase):
    def test_unread_count_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/notifications/unread-count/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unread_count_returns_only_my_unread_known_kind_notifications(self):
        # 2 unread for self.user (1 known kind, 1 unknown kind)
        self._create_notification(
            recipient=self.user, kind="inquiry_message", read=False
        )
        self._create_notification(
            recipient=self.user, kind="unknown_deprecated_kind", read=False
        )
        # 1 read for self.user
        self._create_notification(recipient=self.user, kind="inquiry_reply", read=True)
        # 2 unread for other_user
        self._create_notification(
            recipient=self.other_user, kind="inquiry_message", read=False
        )

        response = self.client.get("/api/v1/notifications/unread-count/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Only the 1 unread known-kind notification of self.user counts
        self.assertEqual(response.data, {"unread_count": 1})


class NotificationMarkReadViewTests(NotificationApiTestCase):
    def test_mark_one_read_sets_read_at_and_returns_updated_item(self):
        notif = self._create_notification(read=False)
        self.assertIsNone(notif.read_at)

        response = self.client.post(f"/api/v1/notifications/{notif.pk}/read/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["unread"])
        self.assertEqual(response.data["id"], notif.pk)

        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)

        # Calling again is harmless and idempotent
        again_response = self.client.post(f"/api/v1/notifications/{notif.pk}/read/")
        self.assertEqual(again_response.status_code, status.HTTP_200_OK)
        self.assertFalse(again_response.data["unread"])

    def test_mark_one_foreign_or_missing_id_returns_identical_404(self):
        foreign = self._create_notification(recipient=self.other_user, read=False)
        missing_pk = foreign.pk + 9999

        foreign_resp = self.client.post(f"/api/v1/notifications/{foreign.pk}/read/")
        missing_resp = self.client.post(f"/api/v1/notifications/{missing_pk}/read/")

        # Both return 404 with identical status and body
        self.assertEqual(foreign_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign_resp.data, missing_resp.data)
        self.assertEqual(foreign_resp.data, {"detail": "Not found."})

        # Other user's row was untouched
        foreign.refresh_from_db()
        self.assertIsNone(foreign.read_at)

    def test_mark_one_unauthenticated_returns_401(self):
        notif = self._create_notification()
        self.client.force_authenticate(user=None)
        response = self.client.post(f"/api/v1/notifications/{notif.pk}/read/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        notif.refresh_from_db()
        self.assertIsNone(notif.read_at)

    def test_mark_one_get_returns_405(self):
        notif = self._create_notification()
        response = self.client.get(f"/api/v1/notifications/{notif.pk}/read/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class NotificationMarkAllReadViewTests(NotificationApiTestCase):
    def test_mark_all_read_marks_only_my_unread_known_kind_notifications(self):
        # 3 unread for self.user (2 known, 1 unknown)
        self._create_notification(
            recipient=self.user, kind="inquiry_message", read=False
        )
        self._create_notification(recipient=self.user, kind="inquiry_reply", read=False)
        self._create_notification(recipient=self.user, kind="unknown_kind", read=False)
        # 1 already read for self.user
        self._create_notification(
            recipient=self.user, kind="inquiry_message", read=True
        )
        # 2 unread for other_user
        other1 = self._create_notification(
            recipient=self.other_user, kind="inquiry_message", read=False
        )
        other2 = self._create_notification(
            recipient=self.other_user, kind="inquiry_reply", read=False
        )

        response = self.client.post("/api/v1/notifications/read-all/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Only the 2 known unread notifications for self.user are updated
        self.assertEqual(response.data, {"updated": 2})

        # Unread count for self.user is now 0
        count_resp = self.client.get("/api/v1/notifications/unread-count/")
        self.assertEqual(count_resp.data, {"unread_count": 0})

        # Other user's rows are completely untouched
        other1.refresh_from_db()
        other2.refresh_from_db()
        self.assertIsNone(other1.read_at)
        self.assertIsNone(other2.read_at)

    def test_mark_all_unauthenticated_returns_401(self):
        notif = self._create_notification()
        self.client.force_authenticate(user=None)
        response = self.client.post("/api/v1/notifications/read-all/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        notif.refresh_from_db()
        self.assertIsNone(notif.read_at)

    def test_mark_all_get_returns_405(self):
        response = self.client.get("/api/v1/notifications/read-all/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class ChannelOnlyKindTests(NotificationApiTestCase):
    """A kind without IN_APP still gets a row from notify(); it must stay hidden."""

    def test_channel_only_kind_is_excluded_everywhere(self):
        telegram_only = Kind(
            name="telegram_only",
            channels=(TELEGRAM,),
            dedupe_key=lambda p: "k",
            link=lambda p: "/x",
            telegram_text=lambda p, url: "text",
        )
        with patch.dict(KINDS, {"telegram_only": telegram_only}):
            hidden = self._create_notification(kind="telegram_only")
            visible = self._create_notification(kind="inquiry_reply")

            listed = self.client.get("/api/v1/notifications/")
            self.assertEqual(
                [item["id"] for item in listed.data["results"]], [visible.pk]
            )

            count = self.client.get("/api/v1/notifications/unread-count/")
            self.assertEqual(count.data, {"unread_count": 1})

            mark_one = self.client.post(f"/api/v1/notifications/{hidden.pk}/read/")
            self.assertEqual(mark_one.status_code, status.HTTP_404_NOT_FOUND)

            mark_all = self.client.post("/api/v1/notifications/read-all/")
            self.assertEqual(mark_all.data, {"updated": 1})

        hidden.refresh_from_db()
        self.assertIsNone(hidden.read_at)


class NotificationContractTests(NotificationApiTestCase):
    """Pins the shape the frontend codes against (NOTIFICATIONS-INTEGRATION.md).

    The frontend drops items whose `type` it doesn't know, so a renamed or new
    type would silently hide them. If one of these fails, the frontend needs the
    same change.
    """

    EXPECTED_TYPES = {
        "inquiry_message": "message",
        "inquiry_reply": "reply",
        "inquiry_unanswered": "message",
        "verification_reviewed": "verify",
    }

    def test_item_has_exactly_the_agreed_keys(self):
        notif = self._create_notification(kind="inquiry_reply")
        data = NotificationSerializer(notif).data
        self.assertEqual(
            set(data),
            {"id", "kind", "type", "unread", "created_at", "title", "body", "cta"},
        )
        self.assertEqual(set(data["cta"]), {"label", "href"})
        self.assertIsInstance(data["id"], int)
        self.assertIsInstance(data["unread"], bool)

    def test_kind_to_type_mapping(self):
        self.assertEqual(set(KINDS), set(self.EXPECTED_TYPES))
        for name, expected_type in self.EXPECTED_TYPES.items():
            with self.subTest(kind=name):
                self.assertEqual(KINDS[name].in_app({}).type, expected_type)

    def test_title_and_body_are_plain_text(self):
        # React escapes on render; pre-escaped text would show "&amp;".
        notif = self._create_notification(
            kind="inquiry_reply",
            payload={"shop_name": "Tom & Jerry <b>", "body": '5 > 3 & "ok"'},
        )
        data = NotificationSerializer(notif).data
        self.assertEqual(data["title"], "Tom & Jerry <b> replied")
        self.assertEqual(data["body"], '5 > 3 & "ok"')


# Payloads shaped like the real notify() call sites, one or two per kind.
SAMPLE_PAYLOADS: dict[str, list[dict]] = {
    "inquiry_message": [
        {"message_id": 1, "inquiry_id": 1, "product_name": "Phone", "body": "Hi"},
        {"message_id": 2, "inquiry_id": 2, "product_name": "", "body": "Hello"},
    ],
    "inquiry_reply": [
        {"message_id": 3, "inquiry_id": 3, "shop_name": "Bole", "body": "In stock"},
    ],
    "inquiry_unanswered": [
        {"inquiry_id": 5, "shop_id": 1, "product_name": "Phone"},
    ],
    "verification_reviewed": [
        {"shop_id": 1, "shop_slug": "bole", "request_id": 1, "state": "approved"},
        {"shop_id": 1, "shop_slug": "bole", "request_id": 2, "state": "rejected"},
    ],
}


class NotificationLinkSafetyTests(TestCase):
    """Links become hrefs in the frontend: always a relative, server-built path."""

    HOSTILE = "//evil.example/x"

    def test_every_kind_has_a_sample(self):
        self.assertEqual(set(SAMPLE_PAYLOADS), set(KINDS))

    def test_every_kind_builds_a_relative_path(self):
        for name, spec in KINDS.items():
            for payload in SAMPLE_PAYLOADS[name]:
                with self.subTest(kind=name, payload=payload):
                    link = spec.link(payload)
                    self.assertTrue(link.startswith("/"), link)
                    self.assertFalse(link.startswith("//"), link)

    def test_free_text_in_payload_never_reaches_the_link(self):
        for name, spec in KINDS.items():
            payload = dict(SAMPLE_PAYLOADS[name][0])
            clean = spec.link(payload)
            for key, value in payload.items():
                if isinstance(value, str):
                    payload[key] = self.HOSTILE
            payload["shop_slug"] = self.HOSTILE
            with self.subTest(kind=name):
                self.assertEqual(spec.link(payload), clean)

    def test_non_numeric_id_is_rejected(self):
        with self.assertRaises(ValueError):
            KINDS["inquiry_reply"].link({"inquiry_id": self.HOSTILE})


@override_settings(DEBUG=True)
class SeedNotificationsCommandTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="seedbuyer", password="pw1")
        self.seller = User.objects.create_user(username="seedseller", password="pw1")
        self.shop = Shop.objects.create(name="Seed Shop", owner=self.seller)
        self.category = Category.objects.create(name="Phones", slug="phones")

    def _product(self, name):
        return Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Acme",
            name=name,
            price="1000.00",
        )

    def _seed(self):
        call_command(
            "seed_notifications",
            buyer="seedbuyer",
            seller="seedseller",
            stdout=io.StringIO(),
        )

    def _kinds(self, user):
        return sorted(
            Notification.objects.filter(recipient=user).values_list("kind", flat=True)
        )

    @override_settings(DEBUG=False)
    def test_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            self._seed()
        self.assertFalse(Inquiry.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_input_errors(self):
        stranger = User.objects.create_user(username="noshop", password="pw1")
        cases = {
            "unknown buyer": {"buyer": "nobody", "seller": "seedseller"},
            "unknown seller": {"buyer": "seedbuyer", "seller": "nobody"},
            "same user": {"buyer": "seedseller", "seller": "seedseller"},
            "seller owns no shop": {"buyer": "seedbuyer", "seller": stranger.username},
        }
        for label, kwargs in cases.items():
            with self.subTest(label), self.assertRaises(CommandError):
                call_command("seed_notifications", **kwargs)
        self.assertFalse(Notification.objects.exists())

    def test_each_side_gets_only_its_own_kinds_with_real_links(self):
        self._product("Phone A")
        self._product("Phone B")
        self._seed()

        self.assertEqual(self._kinds(self.buyer), ["inquiry_reply"])
        self.assertEqual(
            self._kinds(self.seller), ["inquiry_message"] * 2 + ["inquiry_unanswered"]
        )
        # Every link points at an inquiry that exists, on the right side's path.
        prefixes = {
            self.buyer.pk: "/inquiries/",
            self.seller.pk: "/shop/dashboard/inquiries/",
        }
        for notification in Notification.objects.all():
            inquiry_id = notification.payload["inquiry_id"]
            self.assertTrue(Inquiry.objects.filter(pk=inquiry_id).exists())
            prefix = prefixes[notification.recipient_id]
            self.assertEqual(notification.link, f"{prefix}{inquiry_id}")

    def test_shop_without_listings_skips_the_reminder(self):
        self._seed()
        self.assertEqual(self._kinds(self.buyer), ["inquiry_reply"])
        self.assertEqual(self._kinds(self.seller), ["inquiry_message"])

    def test_rerun_continues_the_conversations_without_a_second_reminder(self):
        self._product("Phone A")
        self._seed()
        self._seed()
        self.assertEqual(Inquiry.objects.count(), 2)
        self.assertEqual(self._kinds(self.buyer), ["inquiry_reply"] * 2)
        self.assertEqual(
            self._kinds(self.seller), ["inquiry_message"] * 4 + ["inquiry_unanswered"]
        )


class ViewConfigurationTests(TestCase):
    def test_all_views_declare_throttle_scope_and_is_authenticated(self):
        views = [
            NotificationListView,
            NotificationUnreadCountView,
            NotificationMarkReadView,
            NotificationMarkAllReadView,
        ]
        for view_cls in views:
            with self.subTest(view=view_cls.__name__):
                self.assertEqual(
                    view_cls.throttle_scope,
                    "notifications",
                    f"{view_cls.__name__} must declare throttle_scope = 'notifications'",
                )
                self.assertIn(
                    permissions.IsAuthenticated,
                    view_cls.permission_classes,
                    f"{view_cls.__name__} must require IsAuthenticated",
                )
