from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from shops.models import Shop
from users.models import OTPVerification, User
from users.services import hash_otp_code


class ThrottleResetMixin:
    def setUp(self):
        cache.clear()
        super().setUp()


class TestOTPRequestView(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("otp_request")
        self.user = User.objects.create_user(
            username="sara",
            email="sara@shop.et",
            phone="+251911234567",
            password="TestPassword123!",
        )

    def test_signup_otp_request_sends_email_and_returns_expiry(self):
        mail.outbox.clear()
        response = self.client.post(
            self.url,
            {"email": "newmerchant@shop.et", "mode": "signup"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("expires_in", response.data)
        self.assertIn("resend_in", response.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].subject.lower())
        self.assertEqual(mail.outbox[0].to, ["newmerchant@shop.et"])

    def test_signup_otp_request_for_existing_email_is_rejected(self):
        response = self.client.post(
            self.url,
            {"email": "sara@shop.et", "mode": "signup"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("destination", response.data)

    def test_signin_otp_request_for_unknown_user_returns_404(self):
        mail.outbox.clear()
        response = self.client.post(
            self.url,
            {"email": "unknown@shop.et", "mode": "signin"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(len(mail.outbox), 0)

    def test_signin_otp_request_for_existing_user_succeeds(self):
        mail.outbox.clear()
        response = self.client.post(
            self.url,
            {"email": "sara@shop.et", "mode": "signin"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

    def test_phone_destination_is_rejected_while_sms_is_unavailable(self):
        mail.outbox.clear()
        response = self.client.post(
            self.url,
            {"phone": "0911234567", "mode": "signup"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("destination", response.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_cooldown_is_enforced(self):
        self.client.post(
            self.url,
            {"email": "merchant2@shop.et", "mode": "signup"},
            format="json",
        )
        second = self.client.post(
            self.url,
            {"email": "merchant2@shop.et", "mode": "signup"},
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("resend_in", second.data)


class TestOTPVerifyView(ThrottleResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("otp_verify")
        self.now = timezone.now()

    def test_signup_with_valid_otp_creates_user_and_shop(self):
        email = "abebe@shop.et"
        code = "123456"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {
                "email": email,
                "code": code,
                "mode": "signup",
                "shop_name": "Abebe Electronics",
                "city": "Addis Ababa",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertIn("user", response.data)

        user = User.objects.get(email=email)
        self.assertEqual(user.shops.count(), 1)
        self.assertEqual(user.shops.first().name, "Abebe Electronics")

    def test_signup_without_shop_name_creates_a_buyer_with_no_shop(self):
        email = "buyer@example.com"
        code = "246810"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {"email": email, "code": code, "mode": "signup", "role": "buyer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"]["shops"], [])
        self.assertEqual(Shop.objects.count(), 0)

    def test_signup_stores_full_name(self):
        """The passwordless door creates accounts too, so it takes the name."""
        email = "hanna@example.com"
        code = "112233"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {
                "email": email,
                "code": code,
                "mode": "signup",
                "role": "buyer",
                "full_name": "Hanna Alemu",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(email=email).full_name, "Hanna Alemu")

    def test_signup_as_owner_without_city_is_rejected(self):
        email = "merchant@example.com"
        code = "135791"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {
                "email": email,
                "code": code,
                "mode": "signup",
                "role": "owner",
                "shop_name": "Merkato Mobile",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("city", response.data)

    def test_signin_with_valid_otp_returns_tokens(self):
        user = User.objects.create_user(
            username="kebede",
            email="kebede@shop.et",
            phone="+251911999888",
            password="TestPassword123!",
        )
        Shop.objects.create(owner=user, name="Kebede Shop", city="Hawassa")

        code = "654321"
        OTPVerification.objects.create(
            destination=user.email,
            code_hash=hash_otp_code(code),
            mode="signin",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {"email": user.email, "code": code, "mode": "signin"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertEqual(response.data["user"]["username"], "kebede")

    def test_invalid_code_is_rejected_and_increments_attempts(self):
        email = "chala@shop.et"
        code = "111222"
        otp = OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {"email": email, "code": "999999", "mode": "signup"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("code", response.data)

        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 1)

    def test_exceeding_max_attempts_locks_out(self):
        email = "tigist@shop.et"
        code = "111222"
        otp = OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            attempts=4,
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {"email": email, "code": "000000", "mode": "signup"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

    def test_expired_code_is_rejected(self):
        email = "marta@shop.et"
        code = "111222"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now - timedelta(seconds=5),
            resend_after=self.now - timedelta(minutes=1),
        )

        response = self.client.post(
            self.url,
            {"email": email, "code": code, "mode": "signup"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_code_cannot_be_reused(self):
        email = "dawit@shop.et"
        code = "777888"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        first = self.client.post(
            self.url,
            {"email": email, "code": code, "mode": "signup"},
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(
            self.url,
            {"email": email, "code": code, "mode": "signup"},
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_signup_as_owner_with_phone_populates_user_and_shop_phone(self):
        """A1: phone is passed to verify_otp and populates both User and Shop."""
        email = "owner_with_phone@shop.et"
        code = "123456"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {
                "email": email,
                "code": code,
                "mode": "signup",
                "role": "owner",
                "shop_name": "Phone Shop",
                "city": "Addis Ababa",
                "phone": "0911234567",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email=email)
        self.assertEqual(user.phone, "+251911234567")
        shop = user.shops.first()
        self.assertIsNotNone(shop)
        self.assertEqual(shop.phone, "+251911234567")

    def test_wrong_code_attempts_survive_raise_and_rollbacks(self):
        """B2 regression test: ValidationError outside atomic block ensures attempts persist."""
        email = "counter_test@shop.et"
        code = "111222"
        otp = OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        response = self.client.post(
            self.url,
            {"email": email, "code": "000000", "mode": "signup"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Re-fetch directly by pk to ensure the DB row committed the attempt increment
        re_fetched = OTPVerification.objects.get(pk=otp.pk)
        self.assertEqual(re_fetched.attempts, 1)

    def test_five_wrong_codes_consume_the_code_and_lock_the_destination_out(self):
        """B2: the fifth failure marks the row used, so a sixth guess finds nothing."""
        email = "lockout_test@shop.et"
        code = "111222"
        otp = OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        for _ in range(5):
            res = self.client.post(
                self.url,
                {"email": email, "code": "000000", "mode": "signup"},
                format="json",
            )
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        otp.refresh_from_db()
        self.assertTrue(otp.is_used)
        self.assertEqual(otp.attempts, 5)

        sixth = self.client.post(
            self.url,
            {"email": email, "code": "000000", "mode": "signup"},
            format="json",
        )
        self.assertEqual(sixth.status_code, status.HTTP_400_BAD_REQUEST)
        # The row was consumed on the fifth failure, so this request finds no
        # unused OTP at all and gets the generic message rather than the cap's.
        # Deliberate: it does not confirm to a guesser that they hit the cap.
        self.assertIn("invalid or has expired", str(sixth.data.get("code", "")))

    def test_otp_destination_throttle_caps_at_twenty_per_hour(self):
        """B1: 21 verify calls to one destination in an hour -> the 21st is 429."""
        email = "throttle_destination@shop.et"
        code = "123456"
        OTPVerification.objects.create(
            destination=email,
            code_hash=hash_otp_code(code),
            mode="signup",
            expires_at=self.now + timedelta(minutes=10),
            resend_after=self.now + timedelta(seconds=60),
        )

        for _ in range(20):
            res = self.client.post(
                self.url,
                {"email": email, "code": "000000", "mode": "signup"},
                format="json",
            )
            self.assertNotEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        twenty_first = self.client.post(
            self.url,
            {"email": email, "code": "000000", "mode": "signup"},
            format="json",
        )
        self.assertEqual(twenty_first.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
