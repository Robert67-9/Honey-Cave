"""
Public /api/v1/ endpoints for third-party integrations (sellers' own
software, delivery/logistics partners). Auth via @api_key_required from
api_auth.py -- NOT session-based, unlike the rest of the site.
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
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


import json
from decimal import Decimal
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F
from .models import Order, OrderItem, Product, Branch, calculate_delivery_fee, haversine_km


def _nearest_branch_for_api(lat, lng):
    branches = Branch.objects.filter(is_active=True)
    best, best_dist = None, None
    for b in branches:
        if b.latitude is None or b.longitude is None:
            continue
        d = haversine_km(lat, lng, b.latitude, b.longitude)
        if best_dist is None or d < best_dist:
            best, best_dist = b, d
    return best, best_dist


@csrf_exempt
@api_key_required(scopes=['orders:write'])
def orders_create(request):
    """
    POST /api/v1/orders/
    {
      "customer_email": "buyer@example.com",
      "items": [{"product_id": 24, "quantity": 2}],
      "fulfillment_type": "pickup" | "delivery",
      "phone": "0244123456",
      "full_name": "...",
      # required only for fulfillment_type == "delivery":
      "delivery_address": "...",
      "delivery_digital_address": "GA-184-9021",
      "delivery_landmark": "...",
      "delivery_lat": 5.65, "delivery_lng": -0.19
    }

    Prices, stock, and shipping fee are ALWAYS computed server-side from
    the database -- nothing about cost is ever trusted from the request
    body. Customer must already have a HoneyCave account (looked up by
    email); this endpoint never creates one.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'method_not_allowed'}, status=405)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid_json', 'message': 'Request body must be valid JSON.'}, status=400)

    customer_email = (payload.get('customer_email') or '').strip().lower()
    items_payload  = payload.get('items') or []
    fulfillment_type = payload.get('fulfillment_type', 'pickup')
    phone = (payload.get('phone') or '').strip()
    full_name = (payload.get('full_name') or '').strip()

    if not customer_email:
        return JsonResponse({'error': 'missing_field', 'message': 'customer_email is required.'}, status=400)
    if not items_payload or not isinstance(items_payload, list):
        return JsonResponse({'error': 'missing_field', 'message': 'items must be a non-empty list.'}, status=400)
    if fulfillment_type not in ('pickup', 'delivery'):
        return JsonResponse({'error': 'invalid_field', 'message': 'fulfillment_type must be "pickup" or "delivery".'}, status=400)
    if not phone:
        return JsonResponse({'error': 'missing_field', 'message': 'phone is required.'}, status=400)

    try:
        customer = User.objects.get(email__iexact=customer_email)
    except User.DoesNotExist:
        return JsonResponse({
            'error': 'customer_not_found',
            'message': f'No HoneyCave account found for {customer_email}. The customer must register on the site before an order can be placed for them.',
        }, status=404)

    # Parse + validate item list shape before touching the database.
    parsed_items = []
    for row in items_payload:
        try:
            product_id = int(row.get('product_id'))
            quantity   = int(row.get('quantity'))
        except (TypeError, ValueError, AttributeError):
            return JsonResponse({'error': 'invalid_field', 'message': 'Each item needs integer product_id and quantity.'}, status=400)
        if quantity < 1:
            return JsonResponse({'error': 'invalid_field', 'message': 'quantity must be at least 1.'}, status=400)
        parsed_items.append((product_id, quantity))

    branch = None
    shipping_fee = Decimal('0')
    delivery_lat = delivery_lng = None

    if fulfillment_type == 'delivery':
        delivery_address = (payload.get('delivery_address') or '').strip()
        if not delivery_address:
            return JsonResponse({'error': 'missing_field', 'message': 'delivery_address is required for home delivery.'}, status=400)
        try:
            delivery_lat = float(payload.get('delivery_lat'))
            delivery_lng = float(payload.get('delivery_lng'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'missing_field', 'message': 'delivery_lat and delivery_lng are required for home delivery.'}, status=400)
        if not (-3.5 <= delivery_lng <= 1.5 and 4.5 <= delivery_lat <= 11.5):
            return JsonResponse({'error': 'invalid_field', 'message': 'Delivery coordinates must be within Ghana.'}, status=400)

        branch, distance_km = _nearest_branch_for_api(delivery_lat, delivery_lng)
        if branch is None:
            return JsonResponse({'error': 'no_branch_available', 'message': 'No active branch could service this delivery location.'}, status=422)
        shipping_fee, _eta = calculate_delivery_fee(distance_km)

    # Atomic: lock each product row, verify stock, decrement, create the
    # order + items -- all or nothing. select_for_update prevents two
    # concurrent API orders (or an API order racing a web checkout) from
    # both succeeding past the last unit of stock.
    try:
        with transaction.atomic():
            order_items_to_create = []
            total_price = Decimal('0')

            for product_id, quantity in parsed_items:
                try:
                    product = Product.objects.select_for_update().get(id=product_id, available=True)
                except Product.DoesNotExist:
                    raise ValueError(f'Product {product_id} not found or unavailable.')
                if product.stock < quantity:
                    raise ValueError(f'Insufficient stock for "{product.name}" (requested {quantity}, available {product.stock}).')

                product.stock = F('stock') - quantity
                product.save(update_fields=['stock'])

                total_price += product.price * quantity
                order_items_to_create.append((product, quantity, product.price))

            total_price += shipping_fee

            order = Order.objects.create(
                user=customer,
                branch=branch,
                full_name=full_name or customer.get_full_name() or customer.username,
                email=customer.email,
                phone=phone,
                address=payload.get('delivery_address', '') if fulfillment_type == 'delivery' else '',
                city=payload.get('city', ''),
                fulfillment_type=fulfillment_type,
                delivery_address=payload.get('delivery_address', '') if fulfillment_type == 'delivery' else '',
                delivery_digital_address=payload.get('delivery_digital_address', ''),
                delivery_landmark=payload.get('delivery_landmark', ''),
                delivery_lat=delivery_lat,
                delivery_lng=delivery_lng,
                shipping_fee=shipping_fee,
                total_price=total_price,
                status='pending',
            )
            for product, quantity, price in order_items_to_create:
                OrderItem.objects.create(order=order, product=product, quantity=quantity, price=price)
    except ValueError as e:
        return JsonResponse({'error': 'order_failed', 'message': str(e)}, status=422)

    return JsonResponse({
        'order_id':     order.id,
        'order_number': order.order_number,
        'status':       order.status,
        'total_price':  str(order.total_price),
        'shipping_fee': str(order.shipping_fee),
    }, status=201)
