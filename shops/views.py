from django.shortcuts import get_object_or_404
from rest_framework import mixins, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalog.models import Product, ProductImage
from catalog.services import apply_confirmation, pause_listing, resume_listing

from .models import Shop, VerificationRequest
from .permissions import IsOwnerOrReadOnly
from .serializers import (
    ShopListSerializer,
    ShopProductImageSerializer,
    ShopProductSerializer,
    ShopPublicSerializer,
    ShopSerializer,
    VerificationRequestSerializer,
)

# A ceiling on "confirm all". Not a performance limit -- it is what keeps one
# request from being a whole-catalogue rubber stamp. See CONFIRM_ALL_NOTE in the
# viewset for the part of this that is still an open product question.
CONFIRM_ALL_MAX = 100


class ShopViewSet(viewsets.ModelViewSet):
    queryset = Shop.objects.all()
    lookup_field = "slug"
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]

    def get_serializer_class(self):
        if self.action == "list":
            return ShopListSerializer
        if self.action == "retrieve" and not self._viewer_owns_shop():
            return ShopPublicSerializer
        return ShopSerializer

    def _viewer_owns_shop(self) -> bool:
        # Anonymous short-circuits before get_object(), so the common read path
        # costs no extra query.
        user = self.request.user
        if not user.is_authenticated:
            return False
        return user.is_staff or self.get_object().owner_id == user.pk

    def perform_create(self, serializer):
        if Shop.objects.filter(owner=self.request.user).exists():
            raise serializers.ValidationError(
                {"detail": "This account already has a registered shop."}
            )
        serializer.save(owner=self.request.user)


class ShopProductViewSet(viewsets.ModelViewSet):
    """A merchant's own catalogue, nested under the shop that owns it.

    Nested rather than flat because `Shop.owner` is a plain FK -- one user can
    own several shops, so "the" shop cannot be inferred from the request. The
    slug in the URL says which one.
    """

    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    serializer_class = ShopProductSerializer
    lookup_field = "slug"

    def get_shop(self):
        # Ownership is enforced once, here, for every action. Filtering on owner
        # rather than checking it afterwards makes someone else's shop a 404 and
        # not a 403, so the URL space does not confirm which slugs exist.
        return get_object_or_404(
            Shop, slug=self.kwargs["shop_slug"], owner=self.request.user
        )

    def get_queryset(self):
        return Product.objects.filter(shop=self.get_shop()).select_related(
            "category", "shop"
        )

    def perform_create(self, serializer):
        product = serializer.save(shop=self.get_shop())
        # A new listing opens fresh through the confirmation service, never by
        # setting the column here. `confirmation_status="fresh"` on its own is
        # both a no-op (it is the model default) and a lie: it leaves
        # `last_confirmed_at` NULL, which the sweep reads as stale and the
        # buyer-facing tier reports as "expired". `confirmed=` carries the
        # merchant's own availability answer rather than assuming in-stock.
        apply_confirmation(product, confirmed=product.is_available)

    # --- freshness transitions ------------------------------------------
    #
    # Every one of these goes through `catalog.services`, the same functions the
    # Telegram callbacks call. The web path holds no transition logic of its own:
    # a second copy would drift, and the whole point of a confirmation clock is
    # that every channel writes it the same way.
    #
    # They hang off this viewset rather than a route of their own so they inherit
    # `get_queryset()` -- another shop's product is simply absent, and returns 404
    # without any per-action permission code to get wrong.

    def _transition(self, apply):
        product = self.get_object()
        apply(product)
        product.refresh_from_db()
        return Response(self.get_serializer(product).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, *args, **kwargs):
        """ "Still available" -- the merchant's yes."""
        return self._transition(lambda p: apply_confirmation(p, confirmed=True))

    @action(detail=True, methods=["post"], url_path="mark-sold")
    def mark_sold(self, request, *args, **kwargs):
        """ "Mark sold" -- a no is still an answer, so the clock restarts."""
        return self._transition(lambda p: apply_confirmation(p, confirmed=False))

    @action(detail=True, methods=["post"])
    def pause(self, request, *args, **kwargs):
        """ "Pause" -- hidden without a stock claim, so the clock is untouched."""
        return self._transition(pause_listing)

    @action(detail=True, methods=["post"])
    def resume(self, request, *args, **kwargs):
        """Un-pause. Not in the ticket, but pausing with no way back is a trap."""
        return self._transition(resume_listing)

    @action(detail=False, methods=["post"], url_path="confirm-all")
    def confirm_all(self, request, *args, **kwargs):
        """Confirm several listings at once.

        Body: {"slugs": [...]} -- required and non-empty. Confirming the whole
        catalogue from an empty body was the other option and it is the wrong
        default: the merchant should be looking at what they are vouching for.

        CONFIRM_ALL_NOTE: this endpoint is the one place a merchant can restore
        freshness in bulk, which is also the one place the signal can be rubber
        stamped into meaninglessness. It is capped at CONFIRM_ALL_MAX per call and
        otherwise unlimited -- no cooldown, no daily budget. Whether that is
        acceptable is a product decision, not a technical one, and it is still
        open.
        """
        slugs = request.data.get("slugs")
        if not isinstance(slugs, list) or not slugs:
            raise serializers.ValidationError(
                {"slugs": "Provide a non-empty list of product slugs."}
            )
        if len(slugs) > CONFIRM_ALL_MAX:
            raise serializers.ValidationError(
                {"slugs": f"At most {CONFIRM_ALL_MAX} products per request."}
            )

        # Deduplicate but keep the caller's order, so the response reads back in
        # the order the dashboard sent.
        wanted = list(dict.fromkeys(slugs))
        found = {p.slug: p for p in self.get_queryset().filter(slug__in=wanted)}

        for slug in wanted:
            if slug in found:
                apply_confirmation(found[slug], confirmed=True)

        # Partial success is reported, never raised: one bad slug in a batch of
        # forty must not roll back the thirty-nine that were fine. `not_found`
        # covers both "no such product" and "not yours" -- the caller cannot tell
        # them apart, which is the point.
        return Response(
            {
                "confirmed": [s for s in wanted if s in found],
                "not_found": [s for s in wanted if s not in found],
            }
        )


class ShopProductImageViewSet(viewsets.ModelViewSet):
    """Images for one product, nested under the shop that owns it.

    Replaces an earlier flat `/catalog/productsimage/` viewset whose `get_object`
    returned a `Product`, so a DELETE against it deleted the whole product -- for
    any authenticated user, on any shop.
    """

    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    serializer_class = ShopProductImageSerializer

    def get_product(self):
        # Two filters, one query: the shop must be the caller's, and the product
        # must be that shop's. Anything else is a 404.
        return get_object_or_404(
            Product,
            slug=self.kwargs["product_slug"],
            shop__slug=self.kwargs["shop_slug"],
            shop__owner=self.request.user,
        )

    def get_queryset(self):
        return ProductImage.objects.filter(product=self.get_product()).order_by(
            "-is_primary", "display_order"
        )

    def get_serializer_context(self):
        # The primary-image check needs the product, and on create there is no
        # instance to read it from.
        context = super().get_serializer_context()
        if self.request.user.is_authenticated and "shop_slug" in self.kwargs:
            context["product"] = self.get_product()
        return context

    def perform_create(self, serializer):
        # Deleting an image is handled by the stock destroy(); the post_delete
        # receiver in catalog.models removes the file from storage.
        serializer.save(product=self.get_product())


class VerificationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """The shop owner's verification request and status viewset.

    Enforces ownership at the shop level: any non-owner gets 404, preventing
    cross-shop leakage of private business documents and rejection history.
    """

    serializer_class = VerificationRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_shop(self):
        return get_object_or_404(
            Shop, slug=self.kwargs["shop_slug"], owner=self.request.user
        )

    def get_queryset(self):
        return VerificationRequest.objects.filter(shop=self.get_shop()).order_by(
            "-submited_at", "-id"
        )

    def get_object(self):
        obj = self.get_queryset().first()
        if not obj:
            from django.http import Http404

            raise Http404("No verification request found.")
        return obj

    def retrieve(self, request, *args, **kwargs):
        """Current verification state: not_submitted, pending, approved, or rejected."""
        shop = self.get_shop()
        latest = self.get_queryset().first()

        if shop.is_verified:
            if latest:
                data = self.get_serializer(latest).data
            else:
                data = {
                    "id": None,
                    "shop": shop.id,
                    "is_verified": True,
                    "submitted_at": None,
                    "submited_at": None,
                    "reviewed_at": None,
                    "reviewer": None,
                    "rejection_reason": None,
                    "supporting_detail": "",
                }
            data["state"] = "approved"
            data["is_verified"] = True
            return Response(data)

        if not latest:
            return Response(
                {
                    "id": None,
                    "shop": shop.id,
                    "is_verified": False,
                    "submitted_at": None,
                    "submited_at": None,
                    "reviewed_at": None,
                    "reviewer": None,
                    "state": "not_submitted",
                    "rejection_reason": None,
                    "supporting_detail": "",
                }
            )

        data = self.get_serializer(latest).data
        data["is_verified"] = False
        return Response(data)

    def perform_create(self, serializer):
        shop = self.get_shop()
        if shop.is_verified:
            raise serializers.ValidationError(
                {"detail": "This shop is already verified."}
            )
        if self.get_queryset().filter(state="pending").exists():
            raise serializers.ValidationError(
                {
                    "detail": "A pending verification request already exists for this shop."
                }
            )
        serializer.save(shop=shop)
