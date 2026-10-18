from django.db.models.signals import pre_delete
from django.dispatch import receiver

from catalog.models import Product

from .models import Inquiry


@receiver(pre_delete, sender=Product)
def close_inquiries_that_would_collide(sender, instance, **kwargs):
    """Make room before a deleted product's inquiries lose their product.

    `Inquiry.product` is SET_NULL, so an open inquiry about this product
    becomes a product-less one. If the same buyer already has an open
    product-less inquiry with the shop, that would break the one-open-
    conversation constraint and abort the delete -- so close it first.
    """
    open_general = Inquiry.objects.filter(
        shop=instance.shop_id, product__isnull=True, status=Inquiry.Status.OPEN
    ).values("buyer")
    Inquiry.objects.filter(
        product=instance, status=Inquiry.Status.OPEN, buyer__in=open_general
    ).update(status=Inquiry.Status.CLOSED)
