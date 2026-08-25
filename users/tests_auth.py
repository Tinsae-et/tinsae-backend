"""Tests for the auth hardening: identity, tokens, sessions, passwords.

`users/tests.py` covers registration and /me. This file covers everything that
was added around them.
"""

from unittest import mock

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from shops.models import Shop

from .emails import send_password_reset_email
from .models import User
from .throttles import LoginAccountThrottle, LoginIPThrottle
from .validators import looks_like_phone, normalize_email, normalize_phone

# Shares no substring with the usernames or emails below:
# UserAttributeSimilarityValidator now actually runs at registration (it is a
# no-op when `user` is None, which is how it was previously reached).
PASSWORD = "Kb7#vLq2Wn"
NEW_PASSWORD = "Zt4$mRx9Qp"


class ThrottleResetMixin:
    """Throttle history lives in the cache and outlives a single test."""

    def setUp(self):
        cache.clear()
        super().setUp()


class TestPhoneNormalization(TestCase):
    def test_all_typed_forms_reach_the_same_e164(self):
        for raw in (
            "0911234567",
            "911234567",
            "251911234567",
            "+251911234567",
            "+251 91 123 4567",
            "0911-234-567",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), "+251911234567")

    def test_safaricom_prefix_is_accepted(self):
        self.assertEqual(normalize_phone("0711234567"), "+251711234567")

    def test_landline_is_rejected(self):
        # This field is a login identifier and a merchant's contact of last
        # resort, so it has to be a number a person carries.
        with self.assertRaises(DjangoValidationError):
            normalize_phone("0111234567")

    def test_junk_is_rejected(self):
        for raw in ("abc", "12345", "09112345678", "+1 555 0100"):
            with self.subTest(raw=raw):
                with self.assertRaises(DjangoValidationError):
                    normalize_phone(raw)

    def test_blank_becomes_none_not_empty_string(self):
        # The whole point: "" would collide under the unique constraint, NULL
        # does not.
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_phone(raw))

    def test_looks_like_phone_does_not_raise(self):
        self.assertTrue(looks_like_phone("0911234567"))
        self.assertFalse(looks_like_phone("sara"))
        self.assertFalse(looks_like_phone("0111234567"))


class TestEmailNormalization(TestCase):
    def test_full_lowercasing(self):
        # Django's own normalize_email only lowercases the domain, which would
        # leave these as two distinct rows under a unique constraint.
        self.assertEqual(normalize_email("Sara@Shop.ET"), "sara@shop.et")

    def test_blank_becomes_none(self):
        for raw in (None, "", "  "):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_email(raw))


class TestIdentityConstraints(TestCase):
    def test_many_users_may_have_no_email(self):
        User.objects.create_user(username="a", password=PASSWORD)
        User.objects.create_user(username="b", password=PASSWORD)
        self.assertEqual(User.objects.filter(email__isnull=True).count(), 2)

    def test_many_users_may_have_no_phone(self):
        User.objects.create_user(username="a", password=PASSWORD)
        User.objects.create_user(username="b", password=PASSWORD)
        self.assertEqual(User.objects.filter(phone__isnull=True).count(), 2)

    def test_duplicate_email_is_rejected_by_the_database(self):
        User.objects.create_user(username="a", email="s@shop.et", password=PASSWORD)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="b", email="s@shop.et", password=PASSWORD
                )

    def test_email_uniqueness_is_case_insensitive(self):
        User.objects.create_user(username="a", email="s@shop.et", password=PASSWORD)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # Lowercased on save, so this collides rather than sneaking past.
                User.objects.create_user(
                    username="b", email="S@Shop.ET", password=PASSWORD
                )

    def test_duplicate_phone_is_rejected_by_the_database(self):
        User.objects.create_user(username="a", phone="0911234567", password=PASSWORD)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # Different typed form, same number.
                User.objects.create_user(
                    username="b", phone="+251911234567", password=PASSWORD
                )

    def test_username_uniqueness_is_case_insensitive(self):
        User.objects.create_user(username="sara", password=PASSWORD)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(username="Sara", password=PASSWORD)

    def test_clean_does_not_resurrect_empty_string_email(self):
        # AbstractUser.clean() coerces None to "" via normalize_email; our
        # override undoes it. Without that, the second no-email user collides.
        user = User(username="a")
        user.full_clean(exclude=["password"])
        self.assertIsNone(user.email)


class TestMultiIdentifierLogin(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("token_obtain_pair")
        self.user = User.objects.create_user(
            username="sara",
            email="sara@shop.et",
            phone="0911234567",
            password=PASSWORD,
        )

    def _login(self, identifier, password=PASSWORD, **extra):
        return self.client.post(
            self.url,
            {"identifier": identifier, "password": password},
            format="json",
            **extra,
        )

    def test_login_by_username(self):
        response = self._login("sara")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_login_by_email(self):
        self.assertEqual(self._login("sara@shop.et").status_code, status.HTTP_200_OK)

    def test_login_by_phone_in_every_typed_form(self):
        for raw in ("0911234567", "+251911234567", "251911234567", "911234567"):
            with self.subTest(raw=raw):
                cache.clear()
                self.assertEqual(self._login(raw).status_code, status.HTTP_200_OK)

    def test_login_is_case_insensitive(self):
        self.assertEqual(self._login("SARA").status_code, status.HTTP_200_OK)
        cache.clear()
        self.assertEqual(self._login("Sara@Shop.ET").status_code, status.HTTP_200_OK)

    def test_username_field_is_still_accepted(self):
        # The alias that lets this ship without a coordinated frontend release.
        response = self.client.post(
            self.url, {"username": "sara", "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_missing_identifier_is_a_400(self):
        response = self.client.post(self.url, {"password": PASSWORD}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self._login("sara").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_errors_do_not_distinguish_unknown_identifier_from_bad_password(self):
        """Extends the username-only version of this test in tests.py.

        Each identifier shape must fail the same way, or the shape itself
        becomes the oracle: "unknown email" answering differently from "wrong
        password" reveals which emails hold accounts.
        """
        wrong_password = self._login("sara", password="NotThePassword!1")
        for unknown in ("nobody", "nobody@shop.et", "0911111111"):
            with self.subTest(unknown=unknown):
                cache.clear()
                response = self._login(unknown)
                self.assertEqual(response.status_code, wrong_password.status_code)
                self.assertEqual(response.data, wrong_password.data)


class TestReservedUsernameShapes(ThrottleResetMixin, APITestCase):
    """A username of email or phone shape is unreachable at login.

    Login routes by shape, so such an account could never be signed into — and
    under a naive OR-lookup it would additionally have made the real owner of
    that email ambiguous. Registration refuses both shapes.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("register")

    def _register(self, **overrides):
        data = {
            "username": "merchant",
            "email": "merchant@shop.et",
            "password": PASSWORD,
            "shop_name": "Bole Electronics",
            "city": "Addis Ababa",
            "phone": "0911234567",
        }
        data.update(overrides)
        return self.client.post(self.url, data, format="json")

    def test_email_shaped_username_is_rejected(self):
        response = self._register(username="sara@shop.et")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_phone_shaped_username_is_rejected(self):
        response = self._register(username="0911234567")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_registration_normalizes_phone_to_e164(self):
        self.assertEqual(self._register(phone="0911 234 567").status_code, 201)
        self.assertEqual(User.objects.get(username="merchant").phone, "+251911234567")

    def test_registration_rejects_a_phone_already_in_use(self):
        User.objects.create_user(
            username="other", phone="+251911234567", password=PASSWORD
        )
        response = self._register()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.data)

    def test_registration_rejects_an_invalid_phone(self):
        response = self._register(phone="0111234567")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.data)

    def test_password_similar_to_username_is_rejected(self):
        # Only reachable because the policy now receives a user instance;
        # UserAttributeSimilarityValidator returns immediately when it is None.
        response = self._register(username="bolelectronics", password="bolelectronics1")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)


class TestTokenLifecycle(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="sara", password=PASSWORD)
        self.login_url = reverse("token_obtain_pair")
        self.refresh_url = reverse("token_refresh")

    def _tokens(self):
        response = self.client.post(
            self.login_url,
            {"identifier": "sara", "password": PASSWORD},
            format="json",
        )
        return response.data

    def test_login_records_last_login(self):
        self.assertIsNone(self.user.last_login)
        self._tokens()
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)

    def test_refresh_rotates_the_refresh_token(self):
        first = self._tokens()
        response = self.client.post(
            self.refresh_url, {"refresh": first["refresh"]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("refresh", response.data)
        self.assertNotEqual(response.data["refresh"], first["refresh"])

    def test_consumed_refresh_token_is_rejected(self):
        first = self._tokens()
        self.client.post(self.refresh_url, {"refresh": first["refresh"]}, format="json")
        replay = self.client.post(
            self.refresh_url, {"refresh": first["refresh"]}, format="json"
        )
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_endpoint_is_throttled(self):
        tokens = self._tokens()
        with mock.patch.dict(
            "rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES",
            {"token_refresh": "1/min"},
        ):
            self.client.post(
                self.refresh_url, {"refresh": tokens["refresh"]}, format="json"
            )
            second = self.client.post(
                self.refresh_url, {"refresh": tokens["refresh"]}, format="json"
            )
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class TestLogout(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("logout")
        self.user = User.objects.create_user(username="sara", password=PASSWORD)
        self.other = User.objects.create_user(username="rival", password=PASSWORD)
        self.tokens = self._login("sara")

    def _login(self, username):
        return self.client.post(
            reverse("token_obtain_pair"),
            {"identifier": username, "password": PASSWORD},
            format="json",
        ).data

    def _auth(self, tokens):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    def test_logout_blacklists_the_refresh_token(self):
        self._auth(self.tokens)
        response = self.client.post(
            self.url, {"refresh": self.tokens["refresh"]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)

        self.client.credentials()
        replay = self.client.post(
            reverse("token_refresh"), {"refresh": self.tokens["refresh"]}, format="json"
        )
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_requires_authentication(self):
        response = self.client.post(
            self.url, {"refresh": self.tokens["refresh"]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cannot_revoke_another_users_session(self):
        others = self._login("rival")
        self._auth(self.tokens)
        response = self.client.post(
            self.url, {"refresh": others["refresh"]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_garbage_refresh_token_is_a_400(self):
        self._auth(self.tokens)
        response = self.client.post(self.url, {"refresh": "not-a-token"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TestPasswordChange(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("password_change")
        self.user = User.objects.create_user(
            username="sara", email="sara@shop.et", password=PASSWORD
        )
        self.tokens = self.client.post(
            reverse("token_obtain_pair"),
            {"identifier": "sara", "password": PASSWORD},
            format="json",
        ).data
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}")

    def _change(self, **overrides):
        data = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        data.update(overrides)
        return self.client.post(self.url, data, format="json")

    def test_requires_authentication(self):
        self.client.credentials()
        self.assertEqual(self._change().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_current_password_is_rejected(self):
        response = self._change(current_password="NotIt!23")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("current_password", response.data)

    def test_weak_new_password_is_rejected(self):
        response = self._change(new_password="abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)

    def test_reusing_the_current_password_is_rejected(self):
        response = self._change(new_password=PASSWORD)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_successful_change_updates_the_password(self):
        self.assertEqual(self._change().status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_successful_change_revokes_existing_sessions(self):
        self._change()
        self.client.credentials()
        replay = self.client.post(
            reverse("token_refresh"), {"refresh": self.tokens["refresh"]}, format="json"
        )
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returned_tokens_are_usable(self):
        """The caller must not be logged out by their own password change."""
        response = self._change()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me = self.client.get(reverse("me"))
        self.assertEqual(me.status_code, status.HTTP_200_OK)


class TestPasswordReset(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.request_url = reverse("password_reset")
        self.confirm_url = reverse("password_reset_confirm")
        self.user = User.objects.create_user(
            username="sara", email="sara@shop.et", password=PASSWORD
        )
        Shop.objects.create(
            owner=self.user, name="Bole Electronics", city="Addis Ababa"
        )

    def _request(self, email):
        return self.client.post(self.request_url, {"email": email}, format="json")

    def _confirm(self, **overrides):
        data = {
            "uid": urlsafe_base64_encode(force_bytes(self.user.pk)),
            "token": default_token_generator.make_token(self.user),
            "new_password": NEW_PASSWORD,
        }
        data.update(overrides)
        return self.client.post(self.confirm_url, data, format="json")

    def test_known_email_sends_a_link(self):
        response = self._request("sara@shop.et")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/reset-password/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["sara@shop.et"])

    def test_email_lookup_is_case_insensitive(self):
        self._request("Sara@Shop.ET")
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_email_answers_identically_and_sends_nothing(self):
        known = self._request("sara@shop.et")
        mail.outbox.clear()
        cache.clear()

        unknown = self._request("nobody@shop.et")
        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown.data, known.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_account_without_an_email_cannot_be_mailed(self):
        # Email is optional, so this merchant has no self-service reset path and
        # is an admin-assisted reset instead. The sender must say so rather than
        # blowing up on a None recipient.
        noemail = User.objects.create_user(username="noemail", password=PASSWORD)
        self.assertFalse(send_password_reset_email(noemail))
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_failure_does_not_leak_account_existence(self):
        # A 500 here would distinguish a registered address as loudly as a 404.
        with mock.patch(
            "users.views.send_password_reset_email", side_effect=OSError("smtp down")
        ):
            response = self._request("sara@shop.et")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_confirm_sets_the_new_password(self):
        self.assertEqual(self._confirm().status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_confirm_revokes_existing_sessions(self):
        tokens = self.client.post(
            reverse("token_obtain_pair"),
            {"identifier": "sara", "password": PASSWORD},
            format="json",
        ).data
        self._confirm()
        replay = self.client.post(
            reverse("token_refresh"), {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_cannot_be_reused(self):
        token = default_token_generator.make_token(self.user)
        self.assertEqual(self._confirm(token=token).status_code, status.HTTP_200_OK)
        second = self._confirm(token=token, new_password="Ye8@nWq3Lv")
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tampered_token_is_rejected(self):
        self.assertEqual(
            self._confirm(token="bogus-token").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_tampered_uid_is_rejected(self):
        self.assertEqual(
            self._confirm(uid="bogus-uid").status_code, status.HTTP_400_BAD_REQUEST
        )

    def test_weak_new_password_is_rejected(self):
        response = self._confirm(new_password="abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)

    def test_logging_in_invalidates_an_outstanding_reset_link(self):
        """Django hashes last_login into the reset token, and UPDATE_LAST_LOGIN
        is on — so a successful login consumes any outstanding link.

        Surprising if you have not met it, and desirable: someone who can still
        log in does not need the reset.
        """
        token = default_token_generator.make_token(self.user)
        self.client.post(
            reverse("token_obtain_pair"),
            {"identifier": "sara", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(
            self._confirm(token=token).status_code, status.HTTP_400_BAD_REQUEST
        )


class TestLoginThrottles(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("token_obtain_pair")
        self.user = User.objects.create_user(
            username="sara",
            email="sara@shop.et",
            phone="0911234567",
            password=PASSWORD,
        )

    def _attempt(self, identifier, ip="10.0.0.1"):
        return self.client.post(
            self.url,
            {"identifier": identifier, "password": "wrong"},
            format="json",
            REMOTE_ADDR=ip,
        )

    def test_account_throttle_spans_source_ips(self):
        """The reason the per-account cap exists.

        Under IP-only limiting, spreading one account's guesses across addresses
        was free.
        """
        with mock.patch.object(
            LoginAccountThrottle, "THROTTLE_RATES", {"login_account": "2/min"}
        ):
            self._attempt("sara", ip="10.0.0.1")
            self._attempt("sara", ip="10.0.0.2")
            third = self._attempt("sara", ip="10.0.0.3")
        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_all_three_identifier_shapes_share_one_account_budget(self):
        """Keyed on the resolved user, not the submitted text.

        Keying on the string would hand an attacker three separate budgets
        against the same account.
        """
        with mock.patch.object(
            LoginAccountThrottle, "THROTTLE_RATES", {"login_account": "2/min"}
        ):
            self._attempt("sara")
            self._attempt("sara@shop.et")
            third = self._attempt("0911234567")
        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_different_accounts_do_not_share_a_budget(self):
        User.objects.create_user(username="rival", password=PASSWORD)
        with mock.patch.object(
            LoginAccountThrottle, "THROTTLE_RATES", {"login_account": "2/min"}
        ):
            self._attempt("sara")
            self._attempt("sara")
            self.assertEqual(
                self._attempt("sara").status_code, status.HTTP_429_TOO_MANY_REQUESTS
            )
            other = self._attempt("rival")
        self.assertNotEqual(other.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_ip_throttle_applies_across_accounts(self):
        User.objects.create_user(username="rival", password=PASSWORD)
        with mock.patch.object(
            LoginIPThrottle, "THROTTLE_RATES", {"login_ip": "2/min"}
        ):
            self._attempt("sara")
            self._attempt("rival")
            third = self._attempt("someone-else")
        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
