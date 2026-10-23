"""Participant-scoped reads for inquiries.

Every lookup is filtered by who is asking, so an inquiry that belongs to
someone else is simply absent from the queryset: a foreign ID raises the same
`Inquiry.DoesNotExist` as an ID that was never issued. Views and bot handlers
must fetch inquiries through here, never through `Inquiry.objects` directly.
"""

from datetime import timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone

from catalog.models import ProductImage

from .models import ContactIntent, Inquiry, InquiryMessage


def inquiries_for_buyer(user):
    """The buyer side of the inbox: conversations this user started."""
    return Inquiry.objects.filter(buyer=user).select_related("shop", "product")


def inquiries_for_shop(shop):
    """The shop side of the inbox for one shop."""
    return Inquiry.objects.filter(shop=shop).select_related("buyer", "product")


def inquiries_for_owner(user):
    """The shop side of the inbox across every shop this user owns."""
    return Inquiry.objects.filter(shop__owner=user).select_related(
        "shop", "buyer", "product"
    )


def inquiries_for_participant(user):
    """Every conversation `user` takes part in, as buyer or as shop owner."""
    return Inquiry.objects.filter(Q(buyer=user) | Q(shop__owner=user))


def with_inbox_relations(queryset):
    """Everything `InquirySerializer` reads, so an inbox page costs a fixed
    number of queries however many rows it holds.

    Product images are ordered inside the Prefetch, like the catalogue views:
    re-sorting on the prefetched manager would re-query per product.
    """
    return queryset.select_related(
        "shop", "buyer", "product__shop", "product__category"
    ).prefetch_related(
        "shop__photos",
        "shop__categories",
        Prefetch(
            "product__product_image",
            queryset=ProductImage.objects.order_by("-is_primary", "display_order"),
        ),
        "messages",
    )


def get_inquiry_for_buyer(user, pk):
    return inquiries_for_buyer(user).get(pk=pk)


def get_inquiry_for_shop(shop, pk):
    return inquiries_for_shop(shop).get(pk=pk)


def get_inquiry_for_participant(user, pk):
    return inquiries_for_participant(user).get(pk=pk)


def role_of(user, inquiry):
    """Which side of `inquiry` `user` is on, or None if they are not on it.

    The buyer side wins if a user somehow sits on both; `start_inquiry` refuses
    to let an owner open a conversation with their own shop, so in practice
    that never happens.
    """
    if inquiry.buyer_id == user.pk:
        return InquiryMessage.SenderRole.BUYER
    if inquiry.shop.owner_id == user.pk:
        return InquiryMessage.SenderRole.SHOP
    return None


def messages_for_participant(user, pk):
    """The thread of one inquiry, oldest first, if `user` is on it."""
    inquiry = get_inquiry_for_participant(user, pk)
    return inquiry.messages.all()


def contact_intents_count_for_shop(shop, days: int = 30) -> int:
    """Return count of buyer contact intents for a shop in the last `days` days."""
    since = timezone.now() - timedelta(days=days)
    return ContactIntent.objects.filter(shop=shop, created_at__gte=since).count()


def contact_intents_count_for_product(product, days: int = 30) -> int:
    """Return count of buyer contact intents for a product in the last `days` days."""
    since = timezone.now() - timedelta(days=days)
    return ContactIntent.objects.filter(product=product, created_at__gte=since).count()
