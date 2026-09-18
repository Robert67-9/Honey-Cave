"""
Public /api/v1/ endpoints for third-party integrations (sellers' own
software, delivery/logistics partners). Auth via @api_key_required from
api_auth.py -- NOT session-based, unlike the rest of the site.
"""
from django.http import JsonResponse
from django.core.paginator import Paginator
from .api_auth import api_key_required
from .models import Product


@api_key_required(scopes=['catalog:read'])
def products_list(request):
    """
    GET /api/v1/products/?page=1&page_size=20
    Returns available products with core fields. Paginated -- default 20,
    max 100 per page, to keep this from being used to scrape everything
    in one shot.
    """
    qs = Product.objects.filter(available=True).order_by('id')

    try:
        page_size = min(int(request.GET.get('page_size', 20)), 100)
    except ValueError:
        page_size = 20
    try:
        page_number = max(int(request.GET.get('page', 1)), 1)
    except ValueError:
        page_number = 1

    paginator = Paginator(qs, page_size)
    page = paginator.get_page(page_number)

    results = [
        {
            'id':          p.id,
            'name':        p.name,
            'slug':        p.slug,
            'description': p.description,
            'price':       str(p.price),
            'stock':       p.stock,
            'image':       request.build_absolute_uri(p.image.url) if p.image else None,
        }
        for p in page.object_list
    ]

    return JsonResponse({
        'count':       paginator.count,
        'page':        page.number,
        'num_pages':   paginator.num_pages,
        'page_size':   page_size,
        'results':     results,
    })
