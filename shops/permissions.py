from rest_framework.permissions import SAFE_METHODS, BasePermission

# Every object this class guards reaches its owning user by one of these paths.
# `ProductImage` needs two hops -- image -> product -> shop -> owner -- which is
# why this is a declared list and not a pair of getattrs.
OWNER_PATHS = ("owner", "shop.owner", "product.shop.owner")


class IsOwnerOrReadOnly(BasePermission):
    """Anyone may read; only the owner may write.

    Objects reach their owner by different routes: a `Shop` owns itself, a
    `Product` goes through `.shop`, a `ProductImage` through `.product.shop`.
    Walking a declared list keeps one class for all three. A model with a fourth
    shape resolves to None and is refused -- this fails closed, so a missing path
    is a 403 for the owner (loud, caught by the audit table) and never an opening
    for anyone else.

    Traversing with `getattr(..., None)` rather than catching `AttributeError`
    keeps a genuine error inside an `owner` property from being swallowed and
    silently downgraded to the next path.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        owner = self._owner_of(obj)
        return owner is not None and owner == request.user

    @staticmethod
    def _owner_of(obj):
        for path in OWNER_PATHS:
            target = obj
            for attr in path.split("."):
                target = getattr(target, attr, None)
                if target is None:
                    break
            if target is not None:
                return target
        return None
