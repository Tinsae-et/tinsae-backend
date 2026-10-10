from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class CategoryPagination(PageNumberPagination):
    """Every category in one page.

    Clients build the whole tree (menus, the filter rail) from one response and
    read only `results`; with the default 20 per page, every category past the
    twentieth silently vanished from the site once the taxonomy grew. The list
    is small and bounded by the taxonomy, so one page is safe. The paginated
    shape stays, so existing clients keep working.
    """

    page_size = 500


class ProductSuggestionsPagination(PageNumberPagination):
    page_size = 8
    page_size_query_param = "page_size"
    max_page_size = 24
