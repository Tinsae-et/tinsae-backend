from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from shops.models import Shop

from .models import User
from .serializers import ShopOwnerRegistrationSerializer, UserSerializer


class TestShopOwnerRegistrationSerializer(TestCase):
    """Test ShopOwnerRegistrationSerializer validation"""

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

    def test_validate_duplicate_email(self):
        """Test that duplicate email raises validation error"""
        data = {
            "username": "newuser",
            "email": "existing@tinsae.com",
            "password": "password123",
            "shop_name": "New Shop",
            "city": "Hawassa",
            "phone": "0922345678",
        }
        serializer = ShopOwnerRegistrationSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)

    def test_validate_duplicate_shop_name(self):
        """Test that duplicate shop name raises validation error"""
        data = {
            "username": "newuser",
            "email": "newuser@tinsae.com",
            "password": "password123",
            "shop_name": "Existing Shop",
            "city": "Hawassa",
            "phone": "0922345678",
        }
        serializer = ShopOwnerRegistrationSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("shop_name", serializer.errors)

    def test_valid_registration_data(self):
        """Test that valid data passes serializer validation"""
        data = {
            "username": "newuser",
            "email": "newuser@tinsae.com",
            "password": "password123",
            "shop_name": "New Shop",
            "city": "Hawassa",
            "phone": "0922345678",
        }
        serializer = ShopOwnerRegistrationSerializer(data=data)
        self.assertTrue(serializer.is_valid())

    def test_missing_required_fields(self):
        """Test that missing required fields raise validation errors"""
        data = {
            "username": "newuser",
            "email": "newuser@tinsae.com",
            # Missing password, shop_name, city, phone
        }
        serializer = ShopOwnerRegistrationSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("password", serializer.errors)
        self.assertIn("shop_name", serializer.errors)


class TestUserSerializer(TestCase):
    """Test UserSerializer"""

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
        """Test UserSerializer returns correct user data with shops"""
        serializer = UserSerializer(self.user)
        data = serializer.data

        self.assertEqual(data["username"], "testuser")
        self.assertEqual(data["email"], "test@tinsae.com")
        self.assertEqual(len(data["shops"]), 2)

    def test_user_serializer_read_only_shops(self):
        """Test that shops field is read-only"""
        serializer = UserSerializer(self.user)
        self.assertIn("shops", serializer.fields)
        self.assertTrue(serializer.fields["shops"].read_only)


class TestRegistration(APITestCase):
    """Test ShopOwnerRegistrationView endpoint"""

    def setUp(self):
        self.client = APIClient()
        self.registration_url = reverse("register")
        self.valid_data = {
            "username": "ashenafi",
            "email": "ashenafi@tinsae.com",
            "password": "password123",
            "shop_name": "Tinsae Shop",
            "phone": "090843435",
            "city": "Hawassa",
        }
        # Create existing user/shop for duplicate tests
        self.user = User.objects.create_user(
            username="test", password="password123", email="test@tinsae.com"
        )
        self.shop = Shop.objects.create(
            owner=self.user, city="Adama", phone="094343043", name="Existing Shop"
        )

    def test_successful_registration(self):
        """Test successful shop owner registration"""
        response = self.client.post(
            self.registration_url, self.valid_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("message", response.data)

        # Verify user was created
        self.assertTrue(User.objects.filter(username="ashenafi").exists())

        # Verify shop was created
        user = User.objects.get(username="ashenafi")
        self.assertTrue(Shop.objects.filter(owner=user, name="Tinsae Shop").exists())

    def test_duplicate_email_registration(self):
        """Test registration fails with duplicate email"""
        data = self.valid_data.copy()
        data["email"] = "test@tinsae.com"  # Existing email
        data["shop_name"] = "Different Shop"

        response = self.client.post(self.registration_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_duplicate_shop_name_registration(self):
        """Test registration fails with duplicate shop name"""
        data = self.valid_data.copy()
        data["email"] = "newemail@tinsae.com"
        data["shop_name"] = "Existing Shop"  # Existing shop name

        response = self.client.post(self.registration_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("shop_name", response.data)

    def test_missing_required_fields(self):
        """Test registration fails with missing required fields"""
        incomplete_data = {
            "username": "newuser",
            "email": "newuser@tinsae.com",
            # Missing password, shop_name, city, phone
        }
        response = self.client.post(
            self.registration_url, incomplete_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_password(self):
        """Test registration fails without password"""
        data = self.valid_data.copy()
        del data["password"]

        response = self.client.post(self.registration_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

    def test_missing_shop_name(self):
        """Test registration fails without shop name"""
        data = self.valid_data.copy()
        del data["shop_name"]

        response = self.client.post(self.registration_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("shop_name", response.data)

    def test_missing_city(self):
        """Test registration fails without city"""
        data = self.valid_data.copy()
        del data["city"]

        response = self.client.post(self.registration_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("city", response.data)

    def test_missing_phone(self):
        """Test registration fails without phone"""
        data = self.valid_data.copy()
        del data["phone"]

        response = self.client.post(self.registration_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.data)

    def test_duplicate_username(self):
        """Test registration fails with duplicate username"""
        data = self.valid_data.copy()
        data["username"] = "test"  # Existing username
        data["email"] = "newemail@tinsae.com"
        data["shop_name"] = "Different Shop"

        response = self.client.post(self.registration_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_registration_creates_shop_with_user(self):
        """Test that registration creates both user and shop"""
        response = self.client.post(
            self.registration_url, self.valid_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(username="ashenafi")
        shop = Shop.objects.get(owner=user)

        self.assertEqual(shop.name, "Tinsae Shop")
        self.assertEqual(shop.city, "Hawassa")
        self.assertEqual(shop.phone, "090843435")


class TestGetMyShops(APITestCase):
    """Test GetMyShops endpoint"""

    def setUp(self):
        self.client = APIClient()
        self.me_url = reverse("me")

        # Create a user with shops
        self.user = User.objects.create_user(
            username="shopowner", email="owner@tinsae.com", password="password123"
        )
        self.shop1 = Shop.objects.create(
            owner=self.user, name="Shop 1", city="Addis Ababa", phone="0911111111"
        )
        self.shop2 = Shop.objects.create(
            owner=self.user, name="Shop 2", city="Hawassa", phone="0922222222"
        )

        # Create another user for isolation testing
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
        """Test authenticated user can retrieve their shops"""
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "shopowner")
        self.assertEqual(response.data["email"], "owner@tinsae.com")
        self.assertEqual(len(response.data["shops"]), 2)

    def test_get_my_shops_unauthenticated(self):
        """Test unauthenticated user gets 401"""
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_my_shops_contains_correct_data(self):
        """Test that response contains correct shop data"""
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        shops = response.data["shops"]
        shop_names = [shop["name"] for shop in shops]

        self.assertIn("Shop 1", shop_names)
        self.assertIn("Shop 2", shop_names)

    def test_get_my_shops_isolates_user_data(self):
        """Test that user only sees their own shops"""
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        shops = response.data["shops"]
        shop_names = [shop["name"] for shop in shops]

        self.assertNotIn("Other Shop", shop_names)
        self.assertEqual(len(shops), 2)

    def test_get_my_shops_returns_user_info(self):
        """Test that response includes user information"""
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("username", response.data)
        self.assertIn("email", response.data)
        self.assertIn("shops", response.data)

    def test_get_my_shops_other_user_isolation(self):
        """Test that other users cannot access a user's shop data via the endpoint"""
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "otherowner")
        self.assertEqual(len(response.data["shops"]), 1)
        self.assertEqual(response.data["shops"][0]["name"], "Other Shop")
