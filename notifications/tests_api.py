"""Tests for notifications API endpoints: list, unread count, mark one, mark all."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.test import APIClient

from notifications.models import Notification
from notifications.views import (
    NotificationListView,
    NotificationMarkAllReadView,
    NotificationMarkReadView,
    NotificationUnreadCountView,
)

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
        link="/dashboard/inquiries/1",
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
            link="/dashboard/inquiries/10",
        )
        n_msg_bare = self._create_notification(
            kind="inquiry_message",
            payload={"body": "Hello there"},
            link="/dashboard/inquiries/11",
        )
        n_msg_empty = self._create_notification(
            kind="inquiry_message",
            payload={},
            link="/dashboard/inquiries/12",
        )
        n_msg_long = self._create_notification(
            kind="inquiry_message",
            payload={"body": "x" * 200},
            link="/dashboard/inquiries/13",
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
            link="/dashboard/inquiries/30",
        )
        n_unans_bare = self._create_notification(
            kind="inquiry_unanswered",
            payload={},
            link="/dashboard/inquiries/31",
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
            r["cta"], {"label": "Reply", "href": "/dashboard/inquiries/10"}
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
            r["cta"], {"label": "Reply", "href": "/dashboard/inquiries/30"}
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

    def test_mark_all_get_returns_405(self):
        response = self.client.get("/api/v1/notifications/read-all/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


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
