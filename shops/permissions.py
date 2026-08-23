from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwnerOrReadOnly(BasePermission):
    """Anyone may read; only the owner may write.

    Resolves the owner for two shapes: a `Shop`, which owns itself, and anything
    that reaches a user through `.shop` -- `Product`, so the nested products
    viewset can reuse this unchanged. Dispatching on `getattr` rather than
    catching `AttributeError` keeps a genuine error inside an `owner` property
    from being swallowed and silently downgraded to the shop branch.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        owner = getattr(obj, "owner", None)
        if owner is None:
            owner = getattr(getattr(obj, "shop", None), "owner", None)
        return owner is not None and owner == request.user
