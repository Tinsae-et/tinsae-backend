from rest_framework import serializers

from catalog.models import Product
from catalog.serializers import ProductSerializer
from shops.models import Shop
from shops.serializers import ShopPublicSerializer

from .models import MAX_BODY_LENGTH, ContactChannel, Inquiry, InquiryMessage


class InquirySerializer(serializers.ModelSerializer):
    # `buyer` stays the plain FK id. A nested user here handed every merchant
    # the buyer's email, phone and Telegram chat id; `buyer_name` is all the
    # inbox shows.
    buyer_name = serializers.CharField(source="buyer.username", read_only=True)
    shop = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()

    class Meta:
        model = Inquiry
        fields = [
            "id",
            "buyer",
            "buyer_name",
            "shop",
            "product",
            "status",
            "last_message_at",
            "last_sender_role",
            "buyer_unread",
            "shop_unread",
            "messages",
        ]
        read_only_fields = fields

    def get_shop(self, obj):
        return ShopPublicSerializer(obj.shop, context=self.context).data

    def get_product(self, obj):
        if obj.product is not None:
            return ProductSerializer(obj.product, context=self.context).data
        return None

    def get_messages(self, obj):
        return InquiryMessageSerializer(
            obj.messages.all(), many=True, context=self.context
        ).data


class InquiryMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = InquiryMessage
        fields = [
            "id",
            "inquiry",
            "body",
            "sent_via",
            "sender_role",
            "created_at",
        ]
        read_only_fields = fields


class ShopOrSlugField(serializers.Field):
    def to_internal_value(self, data):
        if isinstance(data, Shop):
            return data
        if isinstance(data, int) or (isinstance(data, str) and data.isdigit()):
            try:
                return Shop.objects.get(pk=int(data))
            except Shop.DoesNotExist:
                raise serializers.ValidationError("Shop does not exist.") from None
        if isinstance(data, str) and data.strip():
            try:
                return Shop.objects.get(slug=data.strip())
            except Shop.DoesNotExist:
                raise serializers.ValidationError("Shop does not exist.") from None
        raise serializers.ValidationError("Shop is required.")

    def to_representation(self, value):
        return value.pk


class ProductOrSlugField(serializers.Field):
    def to_internal_value(self, data):
        if not data:
            return None
        if isinstance(data, Product):
            return data
        if isinstance(data, int) or (isinstance(data, str) and data.isdigit()):
            try:
                return Product.objects.get(pk=int(data))
            except Product.DoesNotExist:
                raise serializers.ValidationError("Product does not exist.") from None
        if isinstance(data, str) and data.strip():
            try:
                return Product.objects.get(slug=data.strip())
            except Product.DoesNotExist:
                raise serializers.ValidationError("Product does not exist.") from None
        raise serializers.ValidationError("Invalid product.")

    def to_representation(self, value):
        return value.pk if value else None


class BuyerInquiryRequestSerializer(serializers.Serializer):
    # No `sent_via`: which door a message came through is the server's to
    # say. The API always records WEB; the bot records TELEGRAM.
    shop = ShopOrSlugField()
    product = ProductOrSlugField(required=False, allow_null=True)
    body = serializers.CharField(max_length=MAX_BODY_LENGTH)


class InquiryReplySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MAX_BODY_LENGTH)


class ContactIntentSerializer(serializers.Serializer):
    shop = ShopOrSlugField(required=False)
    product = ProductOrSlugField(required=False, allow_null=True)
    channel = serializers.ChoiceField(choices=ContactChannel.choices)
    honeypot = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        shop = attrs.get("shop")
        product = attrs.get("product")
        if not shop and product:
            attrs["shop"] = product.shop
        elif not shop:
            raise serializers.ValidationError({"shop": "Shop is required."})
        return attrs
