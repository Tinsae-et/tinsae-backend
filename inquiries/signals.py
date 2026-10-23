from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from catalog.models import Product

from .models import Inquiry


@receiver(pre_delete, sender=Product)
def close_inquiries_that_would_collide(sender, instance, **kwargs):
    """Make room before a deleted product's inquiries lose their product.

    `Inquiry.product` is SET_NULL, so an open inquiry about this product
    becomes a product-less one. If the same buyer already has an open
    product-less inquiry with the shop, that would break the one-open-
    conversation constraint and abort the delete -- so close it first.

    Any *other* open conversation with the shop counts, not only product-less
    ones: when several products go in one delete (admin bulk action, queryset
    delete) every pre_delete runs before any SET_NULL, so two open inquiries
    about two of them would both turn product-less at once.
    """
    other_open = (
        Inquiry.objects.filter(shop=instance.shop_id, status=Inquiry.Status.OPEN)
        .exclude(product=instance)
        .values("buyer")
    )
    Inquiry.objects.filter(
        product=instance, status=Inquiry.Status.OPEN, buyer__in=other_open
    ).update(status=Inquiry.Status.CLOSED, updated_at=timezone.now())
