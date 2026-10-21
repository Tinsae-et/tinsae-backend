from django.contrib.auth import get_user_model
from rest_framework import serializers

from catalog.models import Product
from catalog.serializers import ProductSerializer
from shops.models import Shop
from shops.serializers import ShopPublicSerializer

from .models import MAX_BODY_LENGTH, Inquiry, InquiryMessage

User = get_user_model()


class InquirySerializer(serializers.ModelSerializer):
    shop = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()

    class Meta:
        model = Inquiry
        fields = [
            "id",
            "buyer",
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
        ]


class BuyerInquiryRequestSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all(), required=False, allow_null=True
    )
    body = serializers.CharField(max_length=MAX_BODY_LENGTH)
    sent_via = serializers.ChoiceField(
        choices=InquiryMessage.SentVia.choices,
        default=InquiryMessage.SentVia.WEB,
        required=False,
    )


class InquiryReplySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MAX_BODY_LENGTH)
    sent_via = serializers.ChoiceField(
        choices=InquiryMessage.SentVia.choices,
        default=InquiryMessage.SentVia.WEB,
        required=False,
    )
