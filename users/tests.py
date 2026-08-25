from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import ScopedRateThrottle

from shops.models import Shop

from .models import User
from .serializers import ShopOwnerRegistrationSerializer, UserSerializer
from .throttles import LoginAccountThrottle

# Registration runs AUTH_PASSWORD_VALIDATORS, so payload passwords have to pass
# them; `create_user()` does not, which is why fixtures built directly below
# still pass anything. This value shares no substring with the usernames or
# emails in this module on purpose -- UserAttributeSimilarityValidator compares
# against each of them, and anything containing "tinsae" lands within a rounding
# error of its 0.7 threshold.
VALID_PASSWORD = "Kb7#vLq2Wn"


class ThrottleResetMixin:
    """Throttle history lives in the cache and outlives a single test.

    Registration is capped at 10/hour and this module posts to it well over ten
    times, so without clearing between tests the later ones fail with 429 for
    reasons that have nothing to do with what they assert.
    """

    def setUp(self):
        cache.clear()
        super().setUp()


class TestShopOwnerRegistrationSerializer(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="existing_user",
            email="existing@tinsae.com",
            password="password123",
        )
        self.shop = Shop.objects.create(
            owner=self.user,
            name="Existing Shop",
            city="Addis Ababa",
            phone="0911234567",
        )

    def _data(self, **overrides):
        data = {
            "username": "newuser",
            "email": "newuser@tinsae.com",
            "password": VALID_PASSWORD,
            "shop_name": "New Shop",
            "city": "Hawassa",
            "phone": "0922345678",
        }
        data.update(overrides)
        return data

    def test_valid_registration_data(self):
        serializer = ShopOwnerRegistrationSerializer(data=self._data())
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_duplicate_username_is_rejected(self):
        serializer = ShopOwnerRegistrationSerializer(
            data=self._data(username="existing_user")
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("username", serializer.errors)

    def test_duplicate_email_is_rejected(self):
        # Email is unique when present -- it is a login identifier and the
        # account-recovery channel, so it cannot address two accounts.
        serializer = ShopOwnerRegistrationSerializer(
            data=self._data(email="existing@tinsae.com")
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)

    def test_email_is_optional(self):
        # Deliberately optional: a large share of merchants have no email they
        # read, and forcing the field yields a unique constraint over fake data.
        data = self._data()
        del data["email"]
        serializer = ShopOwnerRegistrationSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_duplicate_shop_name_is_allowed(self):
        # Shop.save() resolves the slug collision; the names may match.
        serializer = ShopOwnerRegistrationSerializer(
            data=self._data(shop_name="Existing Shop")
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_weak_password_is_rejected(self):
        serializer = ShopOwnerRegistrationSerializer(data=self._data(password="abc"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("password", serializer.errors)

    def test_common_password_is_rejected(self):
        # Long enough and not all-numeric, so only CommonPasswordValidator can
        # catch it — which is the validator most easily left unwired.
        serializer = ShopOwnerRegistrationSerializer(
            data=self._data(password="password")
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("password", serializer.errors)

    def test_missing_required_fields(self):
        serializer = ShopOwnerRegistrationSerializer(
            data={"username": "newuser", "email": "newuser@tinsae.com"}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("password", serializer.errors)
        self.assertIn("shop_name", serializer.errors)


class TestUserSerializer(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@tinsae.com", password="password123"
        )
        self.shop1 = Shop.objects.create(
            owner=self.user, name="Shop 1", city="Addis Ababa", phone="0911234567"
        )
        self.shop2 = Shop.objects.create(
            owner=self.user, name="Shop 2", city="Hawassa", phone="0922345678"
        )

    def test_user_serializer_output(self):
        data = UserSerializer(self.user).data
        self.assertEqual(data["username"], "testuser")
        self.assertEqual(data["email"], "test@tinsae.com")
        self.assertEqual(len(data["shops"]), 2)

    def test_password_is_never_serialized(self):
        self.assertNotIn("password", UserSerializer(self.user).data)

    def test_user_serializer_read_only_shops(self):
        serializer = UserSerializer(self.user)
        self.assertIn("shops", serializer.fields)
        self.assertTrue(serializer.fields["shops"].read_only)


class TestRegistration(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.registration_url = reverse("register")
        self.valid_data = {
            "username": "ashenafi",
            "email": "ashenafi@tinsae.com",
            "password": VALID_PASSWORD,
            "shop_name": "Tinsae Shop",
            "phone": "0908434350",
            "city": "Hawassa",
        }
        self.user = User.objects.create_user(
            username="test", password="password123", email="test@tinsae.com"
        )
        self.shop = Shop.objects.create(
            owner=self.user, city="Adama", phone="0943430430", name="Existing Shop"
        )

    def _register(self, **overrides):
        data = self.valid_data.copy()
        data.update(overrides)
        return self.client.post(self.registration_url, data, format="json")

    def test_successful_registration(self):
        response = self._register()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(username="ashenafi")
        shop = Shop.objects.get(owner=user)
        self.assertEqual(shop.name, "Tinsae Shop")
        self.assertEqual(shop.city, "Hawassa")
        # Canonicalised by `validate_phone`, and the same value is written to
        # both the merchant and their shop — they are one number at registration.
        self.assertEqual(shop.phone, "+251908434350")
        self.assertEqual(user.phone, shop.phone)

    def test_new_shop_is_unverified(self):
        self._register()
        shop = Shop.objects.get(name="Tinsae Shop")
        self.assertFalse(shop.is_verified)

    def test_registration_returns_usable_tokens(self):
        response = self._register()
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["username"], "ashenafi")

        # The point of returning them: no second call to /auth/token/ needed.
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me = client.get(reverse("me"))
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data["username"], "ashenafi")

    def test_registration_does_not_leak_password(self):
        response = self._register()
        self.assertNotIn("password", response.data)
        self.assertNotIn("password", response.data["user"])

    def test_duplicate_username_registration(self):
        response = self._register(username="test")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_duplicate_email_registration_is_rejected(self):
        response = self._register(email="test@tinsae.com")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_duplicate_shop_name_registration_succeeds(self):
        response = self._register(shop_name="Existing Shop")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        shops = Shop.objects.filter(name="Existing Shop").order_by("slug")
        self.assertEqual(shops.count(), 2)
        # Same name, distinct slugs — Shop.save() resolves the collision.
        self.assertEqual([s.slug for s in shops], ["existing-shop", "existing-shop-2"])

    def test_weak_password_registration(self):
        response = self._register(password="abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="ashenafi").exists())

    def test_missing_required_fields(self):
        response = self.client.post(
            self.registration_url,
            {"username": "newuser", "email": "newuser@tinsae.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_each_required_field_is_enforced(self):
        for field in ("password", "shop_name", "city", "phone"):
            with self.subTest(field=field):
                data = self.valid_data.copy()
                del data[field]
                response = self.client.post(self.registration_url, data, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_failure_mid_creation_leaves_no_orphan_user(self):
        """The failure mode that matters: an account with nothing to manage.

        It would also be unrecoverable — the username is taken, so the merchant
        could not simply register again.
        """
        with mock.patch(
            "users.serializers.Shop.objects.create",
            side_effect=ValueError("simulated failure"),
        ):
            with self.assertRaises(ValueError):
                self._register()

        self.assertEqual(User.objects.filter(username="ashenafi").count(), 0)
        self.assertEqual(Shop.objects.filter(name="Tinsae Shop").count(), 0)

    def test_registration_is_throttled(self):
        # Each attempt needs its own email and phone: both are unique now, so
        # overriding only the username makes the second call a 400 on the
        # duplicate and the throttle under test is never reached.
        def attempt(n):
            return self._register(
                username=f"merchant{n}",
                email=f"merchant{n}@tinsae.com",
                phone=f"091100000{n}",
            )

        with mock.patch.object(
            ScopedRateThrottle,
            "THROTTLE_RATES",
            {"register": "2/hour", "login": "10/min"},
        ):
            self.assertEqual(attempt(1).status_code, status.HTTP_201_CREATED)
            self.assertEqual(attempt(2).status_code, status.HTTP_201_CREATED)
            self.assertEqual(attempt(3).status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertFalse(User.objects.filter(username="merchant3").exists())


class TestLogin(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.token_url = reverse("token_obtain_pair")
        self.user = User.objects.create_user(
            username="shopowner", email="owner@tinsae.com", password=VALID_PASSWORD
        )

    def test_login_returns_tokens(self):
        response = self.client.post(
            self.token_url,
            {"username": "shopowner", "password": VALID_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_login_errors_do_not_distinguish_unknown_user_from_bad_password(self):
        """Neither response may reveal whether the account exists."""
        unknown = self.client.post(
            self.token_url,
            {"username": "nobody", "password": VALID_PASSWORD},
            format="json",
        )
        wrong_password = self.client.post(
            self.token_url,
            {"username": "shopowner", "password": "NotThePassword!1"},
            format="json",
        )
        self.assertEqual(unknown.status_code, wrong_password.status_code)
        self.assertEqual(unknown.data, wrong_password.data)

    def test_login_is_throttled(self):
        # Login no longer runs on ScopedRateThrottle: it is capped per-IP and
        # per-account at once (users.throttles). This exercises the per-account
        # cap, which is the one that stops a password guessing run.
        with mock.patch.object(
            LoginAccountThrottle, "THROTTLE_RATES", {"login_account": "2/min"}
        ):
            payload = {"username": "shopowner", "password": "wrong"}
            self.client.post(self.token_url, payload, format="json")
            self.client.post(self.token_url, payload, format="json")
            third = self.client.post(self.token_url, payload, format="json")
        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class TestGetMyShops(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.me_url = reverse("me")

        self.user = User.objects.create_user(
            username="shopowner", email="owner@tinsae.com", password="password123"
        )
        self.shop1 = Shop.objects.create(
            owner=self.user, name="Shop 1", city="Addis Ababa", phone="0911111111"
        )
        self.shop2 = Shop.objects.create(
            owner=self.user, name="Shop 2", city="Hawassa", phone="0922222222"
        )

        self.other_user = User.objects.create_user(
            username="otherowner", email="other@tinsae.com", password="password123"
        )
        self.other_shop = Shop.objects.create(
            owner=self.other_user,
            name="Other Shop",
            city="Dire Dawa",
            phone="0933333333",
        )

    def test_get_my_shops_authenticated(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "shopowner")
        self.assertEqual(response.data["email"], "owner@tinsae.com")
        self.assertEqual(len(response.data["shops"]), 2)

    def test_get_my_shops_unauthenticated(self):
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_response_includes_verification_state(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.me_url)
        for shop in response.data["shops"]:
            self.assertIn("is_verified", shop)

    def test_get_my_shops_isolates_user_data(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.me_url)

        shop_names = [shop["name"] for shop in response.data["shops"]]
        self.assertNotIn("Other Shop", shop_names)
        self.assertEqual(len(shop_names), 2)

    def test_get_my_shops_other_user_isolation(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "otherowner")
        self.assertEqual(len(response.data["shops"]), 1)
        self.assertEqual(response.data["shops"][0]["name"], "Other Shop")
