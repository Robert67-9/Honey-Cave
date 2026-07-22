from decimal import Decimal, InvalidOperation
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import timedelta
from .models import Product, Category, Order, OrderItem, Review, REGION_FEES, PaymentSettings, Branch, REGION_CHOICES as _RC, PromoCode, OrderNote, Notification, ProductImage, RiderDelivery, AuditLog, AdminTOTP, Promotion, SiteSettings, HandoffCode, BranchProduct, UserProfile, Rider, BranchAssignment, OfficerUploadRequest, normalize_phone
from .forms import PaymentSettingsForm
from .security import validate_uploaded_image
import json
import requests
from django.core.files.base import ContentFile
import os

# ─── Audit Log helper ────────────────────────────────────────────────

def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def audit_log(request, action, target_repr='', detail=''):
    """
    Insert one immutable AuditLog row.
    Call this immediately after every successful admin mutation.
    Failures are silently swallowed so a logging bug never breaks a real action.
    """
    try:
        AuditLog.objects.create(
            actor=request.user if request.user.is_authenticated else None,
            action=action,
            target_repr=str(target_repr)[:300],
            detail=str(detail)[:2000],
            ip_address=_get_client_ip(request),
        )
    except Exception:
        pass


def admin_required(view_func):
    """Decorator: staff or superuser only. Redirects to the dedicated admin
    login page (/panel/login/) which has its own stricter rate limit scope."""
    return staff_member_required(view_func, login_url='/panel/login/')


# ── Shared input helpers ──────────────────────────────────────────────────────

def _clean_str(post, key, max_len=200, required=True):
    val = post.get(key, '').strip()[:max_len]
    if required and not val:
        raise ValueError(f'"{key}" is required.')
    return val

def _clean_decimal(post, key, min_val=0):
    raw = post.get(key, '').strip()
    try:
        val = Decimal(raw)
        if val < min_val:
            raise ValueError()
        return val
    except (InvalidOperation, ValueError):
        raise ValueError(f'"{key}" must be a valid number ≥ {min_val}.')

def _clean_int(post, key, min_val=0):
    raw = post.get(key, '').strip()
    try:
        val = int(raw)
        if val < min_val:
            raise ValueError()
        return val
    except (ValueError, TypeError):
        raise ValueError(f'"{key}" must be a whole number ≥ {min_val}.')

def _clean_float(post, key, required=False):
    """BUG-05 FIX: Validate and convert lat/lng fields to float with a friendly
    error instead of letting Django crash with a cryptic DB type error."""
    raw = post.get(key, '').strip()
    if not raw:
        if required:
            raise ValueError(f'"{key}" is required.')
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        raise ValueError(f'"{key}" must be a valid decimal number (e.g. 5.6037).')

def _handle_image(request, obj, field='image'):
    """Validate and attach uploaded image or image_url to model instance. Returns error str or None."""
    image_url = request.POST.get('image_url', '').strip()
    f = request.FILES.get(field)
    if f:
        err = validate_uploaded_image(f)
        if err:
            return err
        setattr(obj, field, f)
    elif image_url:
        try:
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            filename = os.path.basename(image_url.split('?')[0]) or 'product.jpg'
            if not filename.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                filename += '.jpg'
            getattr(obj, field).save(filename, ContentFile(response.content), save=False)
        except Exception:
            pass
        obj.image_url = image_url
    return None


def _handle_product_images(request, product, max_images=6):
    """
    Process 0–6 image uploads for a product in a single workflow.

    The form sends files under field names `image_1`, `image_2`, ... `image_6`.
    `image_1` is the primary image (saved on Product.image). The remaining
    slots become ProductImage gallery rows.

    Also accepts a list of `delete_gallery_id` values to remove existing
    gallery images on edit.

    Returns an error string (and aborts) or None on success.

    Important: this MUST be called AFTER product.save() so the product has
    a primary key for ProductImage rows. The primary image (slot 1) is saved
    onto the product instance itself; the caller is responsible for
    product.save() after this returns.
    """
    from .models import ProductImage  # local import to avoid circulars at top

    # 1. Collect uploaded files in slot order, validating each.
    uploads = []
    for slot in range(1, max_images + 1):
        f = request.FILES.get(f'image_{slot}')
        if f:
            err = validate_uploaded_image(f)
            if err:
                return f'Image slot {slot}: {err}'
            uploads.append((slot, f))

    # 2. Optional delete list for existing gallery rows (edit only).
    delete_ids = request.POST.getlist('delete_gallery_id')
    if delete_ids and product.pk:
        ProductImage.objects.filter(product=product, pk__in=delete_ids).delete()

    # 3. Existing gallery count (for cap check)
    existing_gallery_count = ProductImage.objects.filter(product=product).count() if product.pk else 0
    has_primary = bool(product.image) or any(s == 1 for s, _ in uploads)
    primary_slot_count = 1 if has_primary else 0
    total_after_upload = primary_slot_count + existing_gallery_count + sum(
        1 for s, _ in uploads if s != 1
    )
    if total_after_upload > max_images:
        return (f'Too many images. Maximum {max_images} per product '
                f'(currently would be {total_after_upload}). Remove some first.')

    # 4. Apply uploads.
    for slot, f in uploads:
        if slot == 1:
            # Primary image — overwrite product.image
            product.image = f
        else:
            # Gallery rows. sort_order = slot - 1 so they sort after the primary.
            ProductImage.objects.create(
                product=product,
                image=f,
                sort_order=slot - 1,
            )
    return None


def _handle_product_branches(request, product):
    """
    Process the per-branch price/stock/available rows from the product form.

    The form sends three parallel arrays per branch row (matching by index):
        branch_id[]       — branch FK id
        branch_price[]    — selling price at this branch (blank = remove row)
        branch_stock[]    — stock at this branch
        branch_available[]— '1' if checkbox is ticked, missing otherwise

    For each branch:
      - blank price + blank stock + unticked → no row created (or existing row deleted)
      - any non-empty data → row created or updated

    Returns error string (and aborts) or None on success.
    """
    from decimal import Decimal as _D, InvalidOperation as _IE
    branch_ids       = request.POST.getlist('branch_id')
    prices           = request.POST.getlist('branch_price')
    stocks           = request.POST.getlist('branch_stock')
    # Multiple checkboxes with the same name don't all submit when unticked,
    # so we need a separate convention: send "1" hidden, plus checkbox under
    # name `branch_available_<id>` to detect explicit ticks.
    if not branch_ids:
        return None  # no branch grid in the form (e.g. no branches exist yet)

    for i, bid_raw in enumerate(branch_ids):
        try:
            bid = int(bid_raw)
        except (TypeError, ValueError):
            continue
        try:
            branch = Branch.objects.get(pk=bid)
        except Branch.DoesNotExist:
            continue

        price_raw = (prices[i]   if i < len(prices)   else '').strip()
        stock_raw = (stocks[i]   if i < len(stocks)   else '').strip()
        is_avail  = request.POST.get(f'branch_available_{bid}') == '1'

        # If admin left BOTH price and stock blank, treat as "remove"
        if not price_raw and not stock_raw:
            BranchProduct.objects.filter(product=product, branch=branch).delete()
            continue

        # Parse values
        try:
            price = _D(price_raw) if price_raw else _D('0')
            if price < 0:
                raise _IE
        except (_IE, ValueError):
            return f'Invalid price for branch {branch.name}: "{price_raw}"'
        try:
            stock = int(stock_raw) if stock_raw else 0
            if stock < 0:
                raise ValueError
        except ValueError:
            return f'Invalid stock for branch {branch.name}: "{stock_raw}"'

        BranchProduct.objects.update_or_create(
            product=product, branch=branch,
            defaults={'price': price, 'stock': stock, 'is_available': is_avail},
        )
    return None


# ─── Dashboard ───────────────────────────────────────────────────────────────

@admin_required
def admin_dashboard(request):
    today    = timezone.now().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    total_revenue = Order.objects.filter(paid=True).aggregate(Sum('total_price'))['total_price__sum'] or 0
    month_revenue = Order.objects.filter(paid=True, created__date__gte=month_ago).aggregate(Sum('total_price'))['total_price__sum'] or 0
    week_revenue  = Order.objects.filter(paid=True, created__date__gte=week_ago).aggregate(Sum('total_price'))['total_price__sum'] or 0

    stats = {
        'total_orders':     Order.objects.count(),
        'pending_orders':   Order.objects.filter(status='pending').count(),
        'total_products':   Product.objects.count(),
        'low_stock':        Product.objects.filter(stock__lt=5).count(),
        'total_users':      User.objects.count(),
        'total_categories': Category.objects.count(),
        'total_revenue':    total_revenue,
        'month_revenue':    month_revenue,
        'week_revenue':     week_revenue,
    }

    recent_orders      = Order.objects.select_related('user').order_by('-created')[:8]
    low_stock_products = Product.objects.filter(stock__lt=5).order_by('stock')[:5]
    recent_reviews     = Review.objects.select_related('user', 'product').order_by('-created')[:5]

    return render(request, 'mall/admin/dashboard.html', {
        'stats': stats,
        'recent_orders': recent_orders,
        'low_stock_products': low_stock_products,
        'recent_reviews': recent_reviews,
        'site_settings': SiteSettings.load(),
    })


# ─── Products ────────────────────────────────────────────────────────────────

@admin_required
def admin_products(request):
    q   = request.GET.get('q', '')
    cat = request.GET.get('category', '')
    products   = Product.objects.select_related('category').order_by('-created')
    if q:
        products = products.filter(Q(name__icontains=q) | Q(description__icontains=q))
    if cat:
        products = products.filter(category__slug=cat)
    categories = Category.objects.all()
    # PERF-04 FIX: paginate — loading all products in one query is unbounded
    paginator = Paginator(products, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))
    return render(request, 'mall/admin/products.html', {
        'products': page_obj, 'page_obj': page_obj,
        'categories': categories, 'q': q, 'cat': cat,
    })


@admin_required
def admin_product_add(request):
    categories = Category.objects.all()
    if request.method == 'POST':
        try:
            name     = _clean_str(request.POST, 'name', 200)
            slug     = _clean_str(request.POST, 'slug', 200)
            desc     = _clean_str(request.POST, 'description', 5000, required=False)
            price    = _clean_decimal(request.POST, 'price', min_val=Decimal('0.01'))
            stock    = _clean_int(request.POST, 'stock', min_val=0)
            cat_id   = _clean_int(request.POST, 'category', min_val=1)

            # Check slug uniqueness
            if Product.objects.filter(slug=slug).exists():
                raise ValueError(f'A product with slug "{slug}" already exists.')

            p = Product(
                name=name, slug=slug, description=desc,
                price=price, stock=stock, category_id=cat_id,
                available='available' in request.POST,
            )
            # Legacy single-image upload via image_url (backward compat)
            img_err = _handle_image(request, p)
            if img_err:
                raise ValueError(img_err)
            # New 6-slot upload — slot 1 → product.image, slots 2-6 → gallery
            # We need to save the product first to get a pk for gallery rows,
            # but slot 1 (primary) must be set before save. Two-step:
            #   a) Set primary image from slot 1 if present
            primary_upload = request.FILES.get('image_1')
            if primary_upload:
                err = validate_uploaded_image(primary_upload)
                if err:
                    raise ValueError(f'Image slot 1: {err}')
                p.image = primary_upload
            p.save()
            #   b) Now create gallery rows for slots 2–6
            multi_err = _handle_product_images(request, p)
            if multi_err:
                # Rollback: delete the product we just saved so admin can retry
                p.delete()
                raise ValueError(multi_err)
            audit_log(request, 'product_create', f'Product "{p.name}" (id={p.pk})')
            #   c) Per-branch price/stock rows
            br_err = _handle_product_branches(request, p)
            if br_err:
                # Don't roll back the whole product — the create itself
                # succeeded. Just surface the validation error.
                messages.error(request, br_err)
                return redirect('admin_product_edit', pk=p.pk)
            messages.success(request, f'Product "{p.name}" created.')
            return redirect('admin_products')
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/product_form.html', {
        'categories': categories,
        'action': 'Add',
        'branches': Branch.objects.filter(is_active=True).order_by('region', 'name'),
        'branch_products_by_id': {},
    })


@admin_required
def admin_product_edit(request, pk):
    product    = get_object_or_404(Product, pk=pk)
    categories = Category.objects.all()
    if request.method == 'POST':
        try:
            name   = _clean_str(request.POST, 'name', 200)
            slug   = _clean_str(request.POST, 'slug', 200)
            desc   = _clean_str(request.POST, 'description', 5000, required=False)
            price  = _clean_decimal(request.POST, 'price', min_val=Decimal('0.01'))
            stock  = _clean_int(request.POST, 'stock', min_val=0)
            cat_id = _clean_int(request.POST, 'category', min_val=1)

            # Check slug uniqueness (exclude self)
            if Product.objects.filter(slug=slug).exclude(pk=pk).exists():
                raise ValueError(f'A product with slug "{slug}" already exists.')

            product.name        = name
            product.slug        = slug
            product.description = desc
            product.price       = price
            product.stock       = stock
            product.category_id = cat_id
            product.available   = 'available' in request.POST
            img_err = _handle_image(request, product)
            if img_err:
                raise ValueError(img_err)
            # 6-slot upload handling — slot 1 overwrites primary if uploaded
            primary_upload = request.FILES.get('image_1')
            if primary_upload:
                err = validate_uploaded_image(primary_upload)
                if err:
                    raise ValueError(f'Image slot 1: {err}')
                product.image = primary_upload
            product.save()
            multi_err = _handle_product_images(request, product)
            if multi_err:
                raise ValueError(multi_err)
            audit_log(request, 'product_update', f'Product "{product.name}" (id={product.pk})')
            br_err = _handle_product_branches(request, product)
            if br_err:
                messages.error(request, br_err)
                return redirect('admin_product_edit', pk=product.pk)
            messages.success(request, f'Product "{product.name}" updated.')
            return redirect('admin_products')
        except ValueError as e:
            messages.error(request, str(e))
    # Build {branch_id: BranchProduct} for the template to pre-fill rows
    branch_products_by_id = {bp.branch_id: bp for bp in product.branch_pricing.all()} if product.pk else {}
    return render(request, 'mall/admin/product_form.html', {
        'product': product, 'categories': categories, 'action': 'Edit',
        'gallery_images': product.gallery.all() if product.pk else [],
        'branches': Branch.objects.filter(is_active=True).order_by('region', 'name'),
        'branch_products_by_id': branch_products_by_id,
    })


@admin_required
def admin_product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        name = product.name
        product.delete()
        audit_log(request, 'product_delete', f'Product "{name}"')
        messages.success(request, f'Product "{name}" deleted.')
        return redirect('admin_products')
    return render(request, 'mall/admin/confirm_delete.html', {'object': product, 'type': 'Product'})


# ─── Categories ──────────────────────────────────────────────────────────────

@admin_required
def admin_categories(request):
    categories = Category.objects.annotate(product_count=Count('products')).order_by('name')
    return render(request, 'mall/admin/categories.html', {'categories': categories})


@admin_required
def admin_category_add(request):
    if request.method == 'POST':
        try:
            name = _clean_str(request.POST, 'name', 100)
            slug = _clean_str(request.POST, 'slug', 100)
            if Category.objects.filter(slug=slug).exists():
                raise ValueError(f'A category with slug "{slug}" already exists.')
            c = Category(name=name, slug=slug)
            img_err = _handle_image(request, c)
            if img_err:
                raise ValueError(img_err)
            c.save()
            audit_log(request, 'category_create', f'Category "{c.name}" (id={c.pk})')
            messages.success(request, f'Category "{c.name}" created.')
            return redirect('admin_categories')
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/category_form.html', {'action': 'Add'})


@admin_required
def admin_category_edit(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        try:
            name = _clean_str(request.POST, 'name', 100)
            slug = _clean_str(request.POST, 'slug', 100)
            if Category.objects.filter(slug=slug).exclude(pk=pk).exists():
                raise ValueError(f'A category with slug "{slug}" already exists.')
            category.name = name
            category.slug = slug
            img_err = _handle_image(request, category)
            if img_err:
                raise ValueError(img_err)
            category.save()
            audit_log(request, 'category_update', f'Category "{category.name}" (id={category.pk})')
            messages.success(request, f'Category "{category.name}" updated.')
            return redirect('admin_categories')
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/category_form.html', {'category': category, 'action': 'Edit'})


@admin_required
def admin_category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        name = category.name
        category.delete()
        audit_log(request, 'category_delete', f'Category "{name}"')
        messages.success(request, f'Category "{name}" deleted.')
        return redirect('admin_categories')
    return render(request, 'mall/admin/confirm_delete.html', {'object': category, 'type': 'Category'})


# ─── Orders ──────────────────────────────────────────────────────────────────

@admin_required
def admin_orders(request):
    status_filter = request.GET.get('status', '')
    orders = Order.objects.select_related('user', 'branch').order_by('-created')
    if status_filter:
        orders = orders.filter(status=status_filter)
    # PERF-03 FIX: paginate — loading all orders in one query is unbounded
    paginator = Paginator(orders, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))
    return render(request, 'mall/admin/orders.html', {
        'orders':         page_obj,
        'page_obj':       page_obj,
        'status_filter':  status_filter,
        'status_choices': Order.STATUS_CHOICES,
    })


@admin_required
def admin_order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)
    items = order.items.select_related('product')
    notes = order.notes.select_related('staff').all()
    rider = getattr(order, 'rider_delivery', None)

    if request.method == 'POST':
        action = request.POST.get('action', 'status')

        # Add internal note
        if action == 'add_note':
            note_text = request.POST.get('note', '').strip()
            if note_text:
                OrderNote.objects.create(order=order, staff=request.user, note=note_text)
                audit_log(request, 'order_note', f'Order {order.order_number}', note_text[:200])
                messages.success(request, 'Note added.')
            return redirect('admin_order_detail', pk=pk)

        # Assign rider — supports roster pick or ad-hoc, mirrors the officer flow.
        if action == 'assign_rider':
            if order.fulfillment_type != 'delivery':
                messages.error(request, 'Riders can only be assigned to delivery orders.')
                return redirect('admin_order_detail', pk=pk)

            mode = request.POST.get('rider_mode', 'roster')
            rider_record = None
            rider_name = ''
            rider_phone = ''
            is_adhoc = False

            if mode == 'roster':
                rider_id = request.POST.get('rider_id', '').strip()
                if not rider_id:
                    messages.error(request, 'Please pick a rider from the roster.')
                    return redirect('admin_order_detail', pk=pk)
                rider_record = Rider.objects.filter(
                    pk=rider_id, is_active=True,
                ).first()
                if not rider_record:
                    messages.error(request, 'That rider isn\'t available.')
                    return redirect('admin_order_detail', pk=pk)
                # Admin can dispatch any active rider; auto-add the order's
                # branch to the rider's branches if missing.
                if order.branch and not rider_record.branches.filter(pk=order.branch.pk).exists():
                    rider_record.branches.add(order.branch)
                rider_name = rider_record.name
                rider_phone = rider_record.phone
            else:
                rider_name  = (request.POST.get('rider_name')  or '').strip()
                rider_phone = (request.POST.get('rider_phone') or '').strip()
                if not rider_name or not rider_phone:
                    messages.error(request, 'Rider name and phone are required.')
                    return redirect('admin_order_detail', pk=pk)
                # Phone-match against roster
                existing = Rider.find_by_phone(rider_phone, active_only=False)
                if existing:
                    if not existing.is_active:
                        # Admin reactivates inline (admin can do this; officer cannot)
                        existing.is_active = True
                        existing.save(update_fields=['is_active'])
                    rider_record = existing
                    if rider_name and rider_name != existing.name:
                        existing.name = rider_name
                        existing.save(update_fields=['name'])
                    rider_name = existing.name
                    is_adhoc = False
                else:
                    rider_record = Rider.objects.create(
                        name=rider_name,
                        phone=rider_phone,
                        is_active=True,
                        is_verified=True,   # Admin-created → trusted by default
                        created_by=request.user,
                        notes=f'Created by admin via order {order.order_number} dispatch.',
                    )
                    if order.branch:
                        rider_record.branches.add(order.branch)
                    is_adhoc = True

            # Create or update the delivery row
            if rider:
                rider.rider       = rider_record
                rider.rider_name  = rider_name
                rider.rider_phone = rider_phone
                rider.is_adhoc    = is_adhoc
                rider.save()
                messages.success(request, f'Rider updated: {rider_name}.')
            else:
                rider = RiderDelivery.objects.create(
                    order=order,
                    rider=rider_record,
                    is_adhoc=is_adhoc,
                    rider_name=rider_name,
                    rider_phone=rider_phone,
                )
                # Auto-advance status to dispatched
                order.status = 'dispatched'
                order.save()
                audit_log(request, 'rider_assign', f'Order {order.order_number}', f'Rider: {rider_name} ({rider_phone})')
                messages.success(request, f'Rider {rider_name} assigned and order marked as Dispatched.')

                # If the fulfillment officer has already confirmed receipt of this
                # order, the rider can be handed off immediately.
                keeper_confirmed = order.handoff_codes.filter(
                    stage='admin_to_officer', used_at__isnull=False,
                ).exists()
                if keeper_confirmed:
                    from . import handoff as _handoff_svc
                    _handoff_svc.issue_code(
                        order, 'officer_to_rider',
                        issued_to_label=f'Rider: {rider_name}',
                    )

            # Send magic-link portal URL to the rider via WhatsApp + SMS.
            try:
                from . import whatsapp as _wa
                _wa.notify_rider_assigned(rider, request=request)
            except Exception:
                pass

            # Fire notifications
            from .views import _notify_rider_dispatched
            _notify_rider_dispatched(order, rider)
            return redirect('admin_order_detail', pk=pk)

        # Handoff actions
        if action == 'issue_handoff_code':
            stage = request.POST.get('stage', '')
            if stage not in dict(HandoffCode.STAGE_CHOICES):
                messages.error(request, 'Invalid handoff stage.')
            else:
                from . import handoff as _handoff_svc
                # Determine recipient label based on stage
                if stage == 'admin_to_officer':
                    keeper = order.branch.fulfillment_officer if order.branch else None
                    label = f'Fulfillment Officer: {keeper.username}' if keeper else f'Branch: {order.branch}'
                elif stage == 'officer_to_rider' and rider:
                    label = f'Rider: {rider.rider_name}'
                else:
                    label = ''
                handoff = _handoff_svc.issue_code(order, stage, issued_to_label=label)
                audit_log(request, 'handoff_issue', f'Order {order.order_number}', f'Stage: {handoff.get_stage_display()}')
                messages.success(request, f'Code issued for {handoff.get_stage_display()}.')
            return redirect('admin_order_detail', pk=pk)

        if action == 'unlock_handoff':
            try:
                handoff_id = int(request.POST.get('handoff_id', 0))
            except ValueError:
                handoff_id = 0
            handoff = order.handoff_codes.filter(pk=handoff_id).first()
            if handoff is None:
                messages.error(request, 'Handoff code not found.')
            else:
                handoff.locked = False
                handoff.attempts = 0
                handoff.save(update_fields=['locked', 'attempts'])
                audit_log(request, 'handoff_unlock', f'Order {order.order_number}', f'Stage: {handoff.get_stage_display()}')
                messages.success(request, f'{handoff.get_stage_display()} unlocked. Attempts reset.')
            return redirect('admin_order_detail', pk=pk)

        # Status update
        new_status = request.POST.get('status')
        if new_status in dict(Order.STATUS_CHOICES):
            old_status = order.status
            order.status = new_status
            order.save()
            audit_log(request, 'order_status', f'Order {order.order_number}', f'{old_status} → {new_status}')
            messages.success(request, f'Order {order.order_number} status updated to {new_status}.')
            if old_status != new_status:
                _send_status_update_email(order)
                from .views import _notify_customer_status_change
                _notify_customer_status_change(order)
                # WhatsApp customer alert (safe no-op if WhatsApp unconfigured)
                try:
                    from . import whatsapp as _wa
                    _wa.notify_customer_status_change(order)
                except Exception:
                    pass
        else:
            messages.error(request, 'Invalid status.')
        return redirect('admin_order_detail', pk=pk)

    # Build rider portal URL for copying
    rider_portal_url = None
    if rider:
        from django.urls import reverse
        rider_portal_url = request.build_absolute_uri(
            reverse('rider_delivery_portal', args=[rider.token])
        )

    # Build handoff timeline — every code ever issued, newest first.
    handoff_timeline = list(order.handoff_codes.select_related('used_by').order_by('-created_at'))
    # Latest code per stage for the action panels.
    handoff_by_stage = {}
    for h in handoff_timeline:
        if h.stage not in handoff_by_stage:
            handoff_by_stage[h.stage] = h
    # Decide which "issue code" buttons to show:
    #   admin_to_keeper  → always available (admin starts the chain)
    #   keeper_to_rider  → only if rider assigned & no active code yet
    #   rider_to_customer & keeper_to_customer → auto-issued, but admin can re-issue
    can_issue = {
        'admin_to_officer':   True,
        'officer_to_rider':   bool(rider) and order.fulfillment_type == 'delivery',
        'rider_to_customer': bool(rider) and order.fulfillment_type == 'delivery',
        'officer_to_customer': order.fulfillment_type == 'pickup',
    }

    # Rider roster relevant to THIS order's branch.
    # Admin sees ALL active riders (not just branch-restricted) since admins
    # can dispatch any rider; branches will auto-add if needed.
    rider_roster = (Rider.objects
                    .filter(is_active=True)
                    .order_by('-is_verified', 'name'))

    # Diagnose why the chain may not be auto-started yet, so the template
    # can show "Why no code yet?" instead of a vague placeholder.
    from .views import _diagnose_handoff_state
    handoff_diag = _diagnose_handoff_state(order)

    return render(request, 'mall/admin/order_detail.html', {
        'order': order,
        'items': items,
        'notes': notes,
        'status_choices': Order.STATUS_CHOICES,
        'rider': rider,
        'rider_portal_url': rider_portal_url,
        'handoff_timeline':  handoff_timeline,
        'handoff_by_stage':  handoff_by_stage,
        'can_issue_handoff': can_issue,
        'rider_roster':      rider_roster,
        'handoff_diag':      handoff_diag,
    })


def _send_status_update_email(order):
    """Email the customer when their order status changes."""
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings as django_settings

    status_display = order.get_status_display()
    status_icons = {
        'pending':    '⏳',
        'processing': '⚙️',
        'shipped':    '📦',
        'delivered':  '✅',
        'cancelled':  '❌',
    }
    icon = status_icons.get(order.status, '📋')
    customer_name = order.full_name or order.user.get_full_name() or order.user.username

    subject = f'Honey Cave Market — Order {order.order_number} Update: {status_display}'
    plain = (
        f'Hi {customer_name},\n\n'
        f'Your order {order.order_number} has been updated.\n\n'
        f'New status: {icon} {status_display}\n\n'
        f'Log in to track your order: {django_settings.SITE_URL}/my-orders/\n\n'
        f'— Market Team'
    )
    html = f'''<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#FAF7F2;font-family:Arial,sans-serif;">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto;">
  <tr><td style="background:#1A1410;border-radius:12px 12px 0 0;padding:22px 32px;text-align:center;">
    <p style="margin:0;font-size:22px;font-weight:700;color:#C9A84C;font-family:Georgia,serif;">
      MARKET</p>
  </td></tr>
  <tr><td style="background:#fff;padding:28px 32px;border:1px solid #E8E0D4;border-top:none;">
    <p style="font-size:15px;color:#1A1410;">Hi <strong>{customer_name}</strong>,</p>
    <p style="font-size:14px;color:#5a5047;">Your order status has been updated.</p>
    <div style="background:#FAF7F2;border-radius:10px;padding:18px 20px;margin:20px 0;text-align:center;">
      <p style="margin:0 0 4px;font-size:28px;">{icon}</p>
      <p style="margin:0;font-size:18px;font-weight:700;color:#1A1410;">Order {order.order_number}</p>
      <p style="margin:4px 0 0;font-size:15px;color:#C9A84C;font-weight:600;">{status_display}</p>
    </div>
    <p style="text-align:center;margin-top:20px;">
      <a href="{django_settings.SITE_URL}/my-orders/"
         style="background:#C9A84C;color:#1A1410;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">
        View My Orders
      </a>
    </p>
  </td></tr>
  <tr><td style="background:#1A1410;border-radius:0 0 12px 12px;padding:16px 32px;text-align:center;">
    <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.4);">© Market · Ghana</p>
  </td></tr>
</table>
</body></html>'''

    try:
        msg = EmailMultiAlternatives(
            subject=subject, body=plain,
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            to=[order.email],
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=True)
    except Exception:
        pass


@admin_required
def admin_order_delete(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        order.delete()
        audit_log(request, 'order_delete', f'Order #HCM{pk:05d}')
        messages.success(request, f'Order #HCM{pk:05d} deleted.')
        return redirect('admin_orders')
    return render(request, 'mall/admin/confirm_delete.html', {'object': order, 'type': 'Order'})


# ─── Users ───────────────────────────────────────────────────────────────────

@admin_required
def admin_users(request):
    q = request.GET.get('q', '')
    users = User.objects.annotate(order_count=Count('orders')).order_by('-date_joined')
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q))
    return render(request, 'mall/admin/users.html', {'users': users, 'q': q})


@admin_required
def admin_user_detail(request, pk):
    target = get_object_or_404(User, pk=pk)
    orders = Order.objects.filter(user=target).order_by('-created')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'toggle_staff':
            target.is_staff = not target.is_staff
            target.save()
            audit_log(request, 'user_staff', f'User "{target.username}"', f'is_staff={target.is_staff}')
            messages.success(request, f'Staff status updated for {target.username}.')
        elif action == 'toggle_active':
            target.is_active = not target.is_active
            target.save()
            audit_log(request, 'user_active', f'User "{target.username}"', f'is_active={target.is_active}')
            messages.success(request, f'Active status updated for {target.username}.')
        elif action == 'toggle_superuser' and request.user.is_superuser:
            target.is_superuser = not target.is_superuser
            target.save()
            audit_log(request, 'user_superuser', f'User "{target.username}"', f'is_superuser={target.is_superuser}')
            messages.success(request, f'Superuser status updated for {target.username}.')
        return redirect('admin_user_detail', pk=pk)
    return render(request, 'mall/admin/user_detail.html', {'target': target, 'orders': orders})


@admin_required
def admin_user_delete(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target == request.user:
        messages.error(request, 'You cannot delete your own account.')
        return redirect('admin_users')
    if request.method == 'POST':
        username = target.username
        target.delete()
        audit_log(request, 'user_delete', f'User "{username}"')
        messages.success(request, f'User "{username}" deleted.')
        return redirect('admin_users')
    return render(request, 'mall/admin/confirm_delete.html', {'object': target, 'type': 'User'})


# ─── Reviews ─────────────────────────────────────────────────────────────────

@admin_required
def admin_reviews(request):
    reviews = Review.objects.select_related('user', 'product').order_by('-created')
    return render(request, 'mall/admin/reviews.html', {'reviews': reviews})


@admin_required
def admin_review_delete(request, pk):
    review = get_object_or_404(Review, pk=pk)
    if request.method == 'POST':
        review.delete()
        messages.success(request, 'Review deleted.')
        return redirect('admin_reviews')
    return render(request, 'mall/admin/confirm_delete.html', {'object': review, 'type': 'Review'})


# ─── Branches ─────────────────────────────────────────────────────────────────

@admin_required
def admin_branches(request):
    branches = Branch.objects.all().order_by('region', 'name')
    return render(request, 'mall/admin/branches.html', {'branches': branches, 'region_choices': _RC})


@admin_required
def admin_branch_add(request):
    if request.method == 'POST':
        try:
            region = request.POST.get('region', '')
            if region not in dict(_RC):
                raise ValueError('Invalid region selected.')
            b = Branch(
                region        = region,
                branch_type   = request.POST.get('branch_type', 'main'),
                name          = _clean_str(request.POST, 'name', 200),
                address       = _clean_str(request.POST, 'address', 300),
                city          = _clean_str(request.POST, 'city', 100),
                phone         = _clean_str(request.POST, 'phone', 30, required=False),
                email         = _clean_str(request.POST, 'email', 254, required=False),
                opening_hours = _clean_str(request.POST, 'opening_hours', 100, required=False) or 'Mon–Sat: 8am – 8pm | Sun: 10am – 6pm',
                landmark      = _clean_str(request.POST, 'landmark', 200, required=False),
                is_active     = 'is_active' in request.POST,
                latitude      = _clean_float(request.POST, 'latitude'),
                longitude     = _clean_float(request.POST, 'longitude'),
            )
            b.save()
            audit_log(request, 'branch_create', f'Branch "{b.name}" (id={b.pk})')
            messages.success(request, f'Branch "{b.name}" created.')
            return redirect('admin_branches')
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/branch_form.html', {'action': 'Add', 'region_choices': _RC})


@admin_required
def admin_branch_edit(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        try:
            region = request.POST.get('region', '')
            if region not in dict(_RC):
                raise ValueError('Invalid region selected.')
            branch.region        = region
            branch.branch_type   = request.POST.get('branch_type', 'main')
            branch.name          = _clean_str(request.POST, 'name', 200)
            branch.address       = _clean_str(request.POST, 'address', 300)
            branch.city          = _clean_str(request.POST, 'city', 100)
            branch.phone         = _clean_str(request.POST, 'phone', 30, required=False)
            branch.email         = _clean_str(request.POST, 'email', 254, required=False)
            branch.opening_hours = _clean_str(request.POST, 'opening_hours', 100, required=False)
            branch.landmark      = _clean_str(request.POST, 'landmark', 200, required=False)
            branch.is_active     = 'is_active' in request.POST
            branch.latitude      = _clean_float(request.POST, 'latitude')
            branch.longitude     = _clean_float(request.POST, 'longitude')
            branch.save()
            audit_log(request, 'branch_update', f'Branch "{branch.name}" (id={branch.pk})')
            messages.success(request, f'Branch "{branch.name}" updated.')
            return redirect('admin_branches')
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/branch_form.html', {
        'action': 'Edit', 'branch': branch, 'region_choices': _RC,
    })


@admin_required
def admin_branch_delete(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        name = branch.name
        branch.delete()
        audit_log(request, 'branch_delete', f'Branch "{name}"')
        messages.success(request, f'Branch "{name}" deleted.')
        return redirect('admin_branches')
    return render(request, 'mall/admin/confirm_delete.html', {'object': branch, 'type': 'Branch'})


# ─── Fulfillment Officer Management ───────────────────────────────────────────────────
# Admin-friendly UI to register and manage fulfillment officer accounts.
# Each row creates: a Django User + UserProfile.is_fulfillment_officer=True + Branch.fulfillment_officer assignment.
# All three are kept consistent through atomic transactions.

import secrets as _secrets
import string as _string


def _generate_secure_password(length=12):
    """
    Generate a friendly-but-strong random password — easy to read aloud
    over the phone to a new fulfillment officer. Avoids ambiguous characters
    (0/O, 1/l/I) so dictation errors don't lock people out.
    """
    safe_chars = ''.join(c for c in (_string.ascii_letters + _string.digits)
                         if c not in '0O1lI')
    return ''.join(_secrets.choice(safe_chars) for _ in range(length))


@admin_required
def admin_fulfillment_officers(request):
    """List all fulfillment officer accounts with quick stats."""
    # A fulfillment officer is a User with profile.is_fulfillment_officer=True.
    # Order: active first, then by branch name, then by username.
    fulfillment_officers = (User.objects
                    .filter(profile__is_fulfillment_officer=True)
                    .select_related('profile')
                    .prefetch_related('managed_branches')
                    .order_by('-is_active', 'username'))

    # For each, find their assigned branch (one branch via Branch.fulfillment_officer FK)
    rows = []
    for u in fulfillment_officers:
        branch = u.managed_branches.first()  # related_name from Branch.fulfillment_officer
        rows.append({
            'user':   u,
            'branch': branch,
            'phone':  u.profile.phone if hasattr(u, 'profile') else '',
        })

    # Branches without a fulfillment officer — for quick "Assign fulfillment officer" links
    unassigned = Branch.objects.filter(is_active=True, fulfillment_officer__isnull=True).order_by('name')

    return render(request, 'mall/admin/fulfillment_officers.html', {
        'rows':       rows,
        'unassigned': unassigned,
    })


def _generate_username_from_name(full_name, branch_name=''):
    """
    Build a sensible username from a person's full name.

    Strategy: lowercase the name, drop non-letters, append branch slug if
    we can squeeze it in, then add a number suffix if needed for uniqueness.

    Examples:
        "Kwame Asante", branch="Accra Main"  -> "kwame_accra"  (or _2 if taken)
        "Ama"                                -> "ama"          (or _2 if taken)
        "Akua O. Mensah", branch=""          -> "akua_mensah"
    """
    import re
    parts = re.findall(r'[A-Za-z]+', (full_name or '').lower())
    if not parts:
        base = 'keeper'
    elif len(parts) == 1:
        base = parts[0]
    else:
        # First name + surname, joined by underscore
        base = f'{parts[0]}_{parts[-1]}'

    # Branch hint, if room (keep total under ~25 chars)
    if branch_name and len(base) < 18:
        branch_slug = re.findall(r'[A-Za-z]+', branch_name.lower())
        if branch_slug:
            candidate = f'{parts[0]}_{branch_slug[0]}'
            if len(candidate) <= 25:
                base = candidate

    # Find a username not yet taken
    base = base[:25]
    candidate = base
    n = 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f'{base}_{n}'
        n += 1
        if n > 999:
            # Pathological fallback — shouldn't happen in practice
            import secrets
            candidate = f'{base}_{secrets.token_hex(3)}'
            break
    return candidate


@admin_required
def admin_fulfillment_officer_add(request):
    """Register a new fulfillment officer account.

    Simplified form: just Full Name, Phone, Email (optional), Branch.
    Username is auto-generated from the name; password is auto-generated
    and shown once to the admin after save.
    """
    branches_available = (Branch.objects.filter(is_active=True, fulfillment_officer__isnull=True)
                          .order_by('name'))

    if request.method == 'POST':
        try:
            from django.db import transaction

            full_name  = _clean_str(request.POST, 'full_name', 120, required=True)
            phone      = _clean_str(request.POST, 'phone',      30, required=True)
            email      = _clean_str(request.POST, 'email',      254, required=False)
            try:
                branch_id = int(request.POST.get('branch_id', 0))
            except ValueError:
                branch_id = 0

            # Split full name into first + last so they show nicely on the
            # dashboard. Single-word names go entirely into first_name.
            name_parts = full_name.split()
            first_name = name_parts[0][:60]
            last_name  = ' '.join(name_parts[1:])[:60] if len(name_parts) > 1 else ''

            # Email uniqueness — only enforce if provided
            if email and User.objects.filter(email__iexact=email).exists():
                raise ValueError(f'Email "{email}" is already in use by another account.')

            # Validate branch first so we can use its name in the username
            branch = None
            if branch_id:
                branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
                if branch is None:
                    raise ValueError('That branch is no longer available. Pick another.')
                if branch.fulfillment_officer is not None:
                    raise ValueError(f'Branch "{branch.name}" already has a fulfillment officer assigned.')

            # Auto-generate username (uniqueness handled inside the helper)
            username = _generate_username_from_name(
                full_name, branch.name if branch else ''
            )

            # Generate the password BEFORE creating the user — we need to
            # display it to the admin afterward, since they can't read it back.
            generated_password = _generate_secure_password(12)

            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=generated_password,
                    first_name=first_name,
                    last_name=last_name,
                )
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.is_fulfillment_officer = True
                profile.phone = phone
                profile.save()
                if branch:
                    branch.fulfillment_officer = user
                    branch.save(update_fields=['fulfillment_officer'])
                    # Mirror into BranchAssignment as primary+approved so the
                    # officer's portal (which reads from the through-table)
                    # picks them up.
                    BranchAssignment.objects.update_or_create(
                        officer=user, branch=branch,
                        defaults={
                            'role':          'primary',
                            'status':        'approved',
                            'requested_by':  request.user,
                            'decided_by':    request.user,
                            'decided_at':    timezone.now(),
                            'decision_note': 'Assigned by admin at onboarding.',
                        },
                    )

            audit_log(
                request, 'fulfillment_officer_add',
                f'Fulfillment Officer "{username}"',
                f'Branch: {branch.name if branch else "none"}',
            )

            # Show login credentials ONCE on the dashboard via session message —
            # admin must copy them now; we don't store the password in plaintext.
            messages.success(
                request,
                f'✓ Fulfillment Officer "{full_name}" registered. Login credentials — '
                f'Username: {username} · Password: {generated_password}. '
                f'Share these with them now. The password will not be shown again. '
                f'They can change it after first login.'
            )
            return redirect('admin_fulfillment_officers')

        except ValueError as e:
            messages.error(request, str(e))

    return render(request, 'mall/admin/fulfillment_officer_form.html', {
        'action':              'Add',
        'branches_available':  branches_available,
        'fulfillment_officer':         None,
    })


@admin_required
def admin_fulfillment_officer_edit(request, pk):
    """Edit existing fulfillment officer details. Username and password aren't editable here —
    use the dedicated reset-password action for password changes."""
    user = get_object_or_404(User, pk=pk, profile__is_fulfillment_officer=True)
    current_branch = user.managed_branches.first()

    # Branches available: unassigned ones + the current one
    branches_available = list(
        Branch.objects.filter(is_active=True, fulfillment_officer__isnull=True).order_by('name')
    )
    if current_branch and current_branch not in branches_available:
        branches_available = [current_branch] + branches_available

    if request.method == 'POST':
        try:
            from django.db import transaction

            first_name = _clean_str(request.POST, 'first_name', 60, required=True)
            last_name  = _clean_str(request.POST, 'last_name',  60, required=False)
            phone      = _clean_str(request.POST, 'phone',      30, required=True)
            email      = _clean_str(request.POST, 'email',      254, required=False)
            try:
                branch_id = int(request.POST.get('branch_id', 0))
            except ValueError:
                branch_id = 0
            is_active  = 'is_active' in request.POST
            can_upload = 'can_upload_products' in request.POST

            # Products selected to assign to this officer (checkbox list).
            assigned_ids = request.POST.getlist('assigned_products')
            try:
                assigned_ids = [int(x) for x in assigned_ids]
            except (TypeError, ValueError):
                assigned_ids = []

            # Email uniqueness — exclude the current user
            if email and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                raise ValueError(f'Email "{email}" is already in use by another account.')

            new_branch = None
            if branch_id:
                new_branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
                if new_branch is None:
                    raise ValueError('That branch is no longer available.')
                if new_branch.fulfillment_officer and new_branch.fulfillment_officer != user:
                    raise ValueError(f'Branch "{new_branch.name}" already has a different fulfillment officer.')

            with transaction.atomic():
                # Update User core fields
                user.first_name = first_name
                user.last_name  = last_name
                user.email      = email
                user.is_active  = is_active
                user.save()
                # Update profile
                user.profile.phone = phone
                user.profile.can_upload_products = can_upload
                user.profile.save()
                # Assigned products (responsible-for list)
                if assigned_ids:
                    valid_ids = list(
                        Product.objects.filter(pk__in=assigned_ids).values_list('pk', flat=True)
                    )
                    user.profile.assigned_products.set(valid_ids)
                else:
                    user.profile.assigned_products.clear()
                # Branch reassignment
                if current_branch and current_branch != new_branch:
                    current_branch.fulfillment_officer = None
                    current_branch.save(update_fields=['fulfillment_officer'])
                    # Revoke the matching BranchAssignment (preserve audit trail)
                    BranchAssignment.objects.filter(
                        officer=user, branch=current_branch, status='approved',
                    ).update(
                        status='revoked',
                        decided_at=timezone.now(),
                        decided_by=request.user,
                        decision_note='Revoked: officer reassigned to a different primary branch.',
                    )
                if new_branch:
                    new_branch.fulfillment_officer = user
                    new_branch.save(update_fields=['fulfillment_officer'])
                    BranchAssignment.objects.update_or_create(
                        officer=user, branch=new_branch,
                        defaults={
                            'role':          'primary',
                            'status':        'approved',
                            'requested_by':  request.user,
                            'decided_by':    request.user,
                            'decided_at':    timezone.now(),
                            'decision_note': 'Assigned by admin via officer edit.',
                        },
                    )

            audit_log(
                request, 'fulfillment_officer_edit',
                f'Fulfillment Officer "{user.username}"',
                f'Branch: {new_branch.name if new_branch else "none"}',
            )
            messages.success(request, f'Fulfillment Officer "{user.username}" updated.')
            return redirect('admin_fulfillment_officers')

        except ValueError as e:
            messages.error(request, str(e))

    all_products = (Product.objects
                    .select_related('category')
                    .order_by('category__name', 'name'))
    assigned_product_ids = set(
        user.profile.assigned_products.values_list('pk', flat=True)
    )

    upload_req = (OfficerUploadRequest.objects
                  .filter(officer=user).order_by('-created').first())

    return render(request, 'mall/admin/fulfillment_officer_form.html', {
        'action':              'Edit',
        'fulfillment_officer':         user,
        'current_branch':      current_branch,
        'branches_available':  branches_available,
        'all_products':        all_products,
        'assigned_product_ids': assigned_product_ids,
        'upload_req':          upload_req,
        'can_upload_now':      user.profile.can_upload_products,
    })


@admin_required
def admin_officer_upload_access_action(request, pk):
    """
    Admin proactively manages an officer's upload access from the officer
    edit page — without waiting for the officer to request it. Two actions:
      - require_payment: set a price → officer must pay before uploading.
      - approve_free:    grant upload access immediately, no charge.
      - revoke:          turn access off.
    """
    user = get_object_or_404(User, pk=pk, profile__is_fulfillment_officer=True)
    if request.method != 'POST':
        return redirect('admin_fulfillment_officer_edit', pk=pk)

    from .notify import notify
    action = request.POST.get('upload_action', '')
    note = _clean_str(request.POST, 'upload_note', 300, required=False)

    if action == 'require_payment':
        raw = (request.POST.get('upload_amount') or '').strip()
        try:
            amount = Decimal(raw)
        except (InvalidOperation, TypeError):
            amount = Decimal('-1')
        if amount <= 0:
            messages.error(request, 'Enter a valid upload price greater than 0.')
            return redirect('admin_fulfillment_officer_edit', pk=pk)
        # Reuse an open request if one exists, else create a new one.
        req = (OfficerUploadRequest.objects
               .filter(officer=user)
               .exclude(status__in=['paid', 'approved_free'])
               .order_by('-created').first())
        if req is None:
            req = OfficerUploadRequest(officer=user)
        req.status = 'payment_required'
        req.amount = amount
        req.admin_note = note
        req.decided_by = request.user
        req.decided_at = timezone.now()
        req.save()
        user.profile.can_upload_products = False
        user.profile.save(update_fields=['can_upload_products'])
        try:
            notify(user, notif_type='stock_alert',
                   title='💳 Payment required for upload access',
                   message=f'Pay GH₵{amount:.2f} from your Upload Access page to unlock product uploads.'
                           + (f' Note: {note}' if note else ''),
                   link='/officer/upload-access/')
        except Exception:
            pass
        messages.success(request, f'{user.username} must now pay GH₵{amount:.2f} to upload.')

    elif action == 'approve_free':
        req = OfficerUploadRequest.objects.create(
            officer=user, status='approved_free', admin_note=note,
            decided_by=request.user, decided_at=timezone.now(),
        )
        user.profile.can_upload_products = True
        user.profile.save(update_fields=['can_upload_products'])
        try:
            notify(user, notif_type='stock_alert',
                   title='✅ Upload access granted (free)',
                   message='You can upload products now.' + (f' Note: {note}' if note else ''),
                   link='/officer/product-upload/')
        except Exception:
            pass
        messages.success(request, f'{user.username} can now upload for free.')

    elif action == 'revoke':
        user.profile.can_upload_products = False
        user.profile.save(update_fields=['can_upload_products'])
        OfficerUploadRequest.objects.filter(officer=user).exclude(
            status='rejected').update(status='rejected', admin_note='Access revoked by admin.',
                                      decided_by=request.user, decided_at=timezone.now())
        messages.success(request, f'Upload access revoked for {user.username}.')

    return redirect('admin_fulfillment_officer_edit', pk=pk)


@admin_required
def admin_fulfillment_officer_reset_password(request, pk):
    """Generate a new password for the fulfillment officer and show it once."""
    user = get_object_or_404(User, pk=pk, profile__is_fulfillment_officer=True)
    if request.method == 'POST':
        new_password = _generate_secure_password(12)
        user.set_password(new_password)
        user.save(update_fields=['password'])
        audit_log(request, 'fulfillment_officer_reset_password', f'Fulfillment Officer "{user.username}"')
        messages.success(
            request,
            f'✓ New password for "{user.username}": {new_password} — share this with them now. '
            f'It will not be shown again.'
        )
        return redirect('admin_fulfillment_officers')
    return render(request, 'mall/admin/confirm_action.html', {
        'object': user,
        'title':  'Reset Fulfillment Officer Password',
        'message': (
            f'Generate a new random password for "{user.username}"? '
            f'Their old password will stop working immediately. The new one will be shown once.'
        ),
        'cancel_url': 'admin_fulfillment_officers',
    })


@admin_required
def admin_fulfillment_officer_deactivate(request, pk):
    """Deactivate (don't delete) a fulfillment officer. Frees up their branch."""
    user = get_object_or_404(User, pk=pk, profile__is_fulfillment_officer=True)
    if request.method == 'POST':
        from django.db import transaction
        with transaction.atomic():
            user.is_active = False
            user.save(update_fields=['is_active'])
            # Free the branch they were managing
            for branch in user.managed_branches.all():
                branch.fulfillment_officer = None
                branch.save(update_fields=['fulfillment_officer'])
            # Revoke ALL their BranchAssignments (primary + secondary)
            BranchAssignment.objects.filter(
                officer=user, status__in=('approved', 'pending'),
            ).update(
                status='revoked',
                decided_at=timezone.now(),
                decided_by=request.user,
                decision_note='Revoked: officer account deactivated.',
            )
        audit_log(request, 'fulfillment_officer_deactivate', f'Fulfillment Officer "{user.username}"')
        messages.success(request, f'Fulfillment Officer "{user.username}" deactivated and unlinked from their branch.')
        return redirect('admin_fulfillment_officers')
    return render(request, 'mall/admin/confirm_action.html', {
        'object': user,
        'title':  'Deactivate Fulfillment Officer',
        'message': (
            f'Deactivate "{user.username}"? They won\'t be able to log in. '
            f'Their branch will be freed for assignment. This is reversible — '
            f'you can re-activate them later from the edit page.'
        ),
        'cancel_url': 'admin_fulfillment_officers',
    })


# ─── Payment Settings ─────────────────────────────────────────────────────────

@admin_required
def admin_payment_settings(request):
    """
    List all configured payment methods. Each row is annotated with the
    a "not yet wired" badge so admins know not to expect customers to be
    able to pay through it, even when the row is_active=True.
    """
    from .payments.dispatch import GATEWAY_REGISTRY
    rows = list(PaymentSettings.objects.all().order_by('-is_active', '-updated_at'))
    for ps in rows:
        cls = GATEWAY_REGISTRY.get(ps.provider)
        ps.adapter_ready = bool(cls and getattr(cls, 'is_ready', False))
    return render(request, 'mall/admin/payment_settings.html', {'settings_list': rows})


@admin_required
def admin_payment_settings_add(request):
    if request.method == 'POST':
        form = PaymentSettingsForm(request.POST)
        if form.is_valid():
            try:
                ps = form.save()
                messages.success(request, f'✅ Payment method "{ps.get_provider_display()}" added successfully.')
                return redirect('admin_payment_settings')
            except Exception as e:
                messages.error(request, f'❌ Error saving payment method: {str(e)}')
        else:
            # Show form errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'❌ {field}: {error}')
    else:
        form = PaymentSettingsForm()
    
    return render(request, 'mall/admin/payment_settings_form.html', {'form': form, 'action': 'Add'})


@admin_required
def admin_payment_settings_edit(request, pk):
    ps = get_object_or_404(PaymentSettings, pk=pk)
    if request.method == 'POST':
        form = PaymentSettingsForm(request.POST, instance=ps)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, '✅ Payment method updated successfully.')
                return redirect('admin_payment_settings')
            except Exception as e:
                messages.error(request, f'❌ Error updating payment method: {str(e)}')
        else:
            # Show form errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'❌ {field}: {error}')
    else:
        form = PaymentSettingsForm(instance=ps)
    
    return render(request, 'mall/admin/payment_settings_form.html', {'form': form, 'action': 'Edit', 'ps': ps})


@admin_required
def admin_payment_settings_delete(request, pk):
    ps = get_object_or_404(PaymentSettings, pk=pk)
    if request.method == 'POST':
        ps.delete()
        messages.success(request, 'Payment method deleted.')
        return redirect('admin_payment_settings')
    return render(request, 'mall/admin/confirm_delete.html', {'object': ps, 'type': 'Payment Method'})


@admin_required
def admin_payment_test(request, pk):
    """Show Paystack key info and link to Paystack dashboard for testing."""
    ps = get_object_or_404(PaymentSettings, pk=pk)
    public_key = ps.account_number  # stored in account_number field
    is_live = public_key.startswith('pk_live_')
    is_test = public_key.startswith('pk_test_')

    return render(request, 'mall/admin/payment_test.html', {
        'ps': ps,
        'public_key': public_key,
        'is_live': is_live,
        'is_test': is_test,
    })


@admin_required
def admin_review_toggle(request, pk):
    """Toggle a review's is_approved flag without deleting it."""
    review = get_object_or_404(Review, pk=pk)
    if request.method == 'POST':
        from .models import Review as _R
        review.is_approved = not review.is_approved
        review.save(update_fields=['is_approved'])
        status = 'visible' if review.is_approved else 'hidden'
        audit_log(request, 'review_toggle', f'Review #{review.pk} by {review.user.username}', f'is_approved={review.is_approved}')
        messages.success(request, f'Review by {review.user.username} is now {status}.')
    return redirect('admin_reviews')


# ─── FEAT-03: CSV Export ──────────────────────────────────────────────────────

import csv
from django.http import HttpResponse

@admin_required
def admin_export_orders_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="market_orders.csv"'
    writer = csv.writer(response)
    writer.writerow(['Order No.', 'Date', 'Customer', 'Email', 'Phone', 'Status',
                     'Fulfillment', 'Region', 'Subtotal', 'Shipping', 'Discount', 'Total', 'Paid'])
    orders = Order.objects.select_related('user').order_by('-created')
    status_filter = request.GET.get('status', '')
    if status_filter:
        orders = orders.filter(status=status_filter)
    for o in orders:
        writer.writerow([
            o.id, o.created.strftime('%Y-%m-%d %H:%M'),
            o.user.username, o.email, o.phone,
            o.status, o.fulfillment_type,
            o.get_region_display_name(),
            o.subtotal(), o.shipping_fee,
            getattr(o, 'discount_amount', 0),
            o.total_price,
            'Yes' if o.paid else 'No',
        ])
    return response


@admin_required
def admin_export_products_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="market_products.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Name', 'Category', 'Price', 'Stock', 'Available', 'Slug', 'Created'])
    for p in Product.objects.select_related('category').order_by('-created'):
        writer.writerow([p.id, p.name, p.category.name, p.price, p.stock,
                         'Yes' if p.available else 'No', p.slug,
                         p.created.strftime('%Y-%m-%d')])
    return response


@admin_required
def admin_export_users_csv(request):
    from django.contrib.auth.models import User as _User
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="market_users.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Username', 'Email', 'First Name', 'Last Name', 'Staff', 'Active', 'Joined', 'Order Count'])
    users = _User.objects.annotate(order_count=Count('orders')).order_by('-date_joined')
    for u in users:
        writer.writerow([u.id, u.username, u.email, u.first_name, u.last_name,
                         'Yes' if u.is_staff else 'No',
                         'Yes' if u.is_active else 'No',
                         u.date_joined.strftime('%Y-%m-%d'),
                         u.order_count])
    return response


# ─── FEAT-06: Product Image Gallery (Admin) ───────────────────────────────────

@admin_required
def admin_product_gallery(request, pk):
    product = get_object_or_404(Product, pk=pk)
    images  = product.gallery.all()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            f = request.FILES.get('image')
            if f:
                from .security import validate_uploaded_image
                err = validate_uploaded_image(f)
                if err:
                    messages.error(request, err)
                else:
                    sort_order = request.POST.get('sort_order', 0)
                    alt_text   = request.POST.get('alt_text', '')
                    ProductImage.objects.create(
                        product=product, image=f,
                        sort_order=int(sort_order) if str(sort_order).isdigit() else 0,
                        alt_text=alt_text[:200],
                    )
                    messages.success(request, 'Image added to gallery.')
        elif action == 'delete':
            img_id = request.POST.get('image_id')
            ProductImage.objects.filter(pk=img_id, product=product).delete()
            messages.success(request, 'Image removed.')
        elif action == 'reorder':
            for key, val in request.POST.items():
                if key.startswith('order_'):
                    img_id = key.replace('order_', '')
                    try:
                        ProductImage.objects.filter(pk=int(img_id), product=product).update(sort_order=int(val))
                    except (ValueError, TypeError):
                        pass
            messages.success(request, 'Gallery order updated.')
        return redirect('admin_product_gallery', pk=pk)
    return render(request, 'mall/admin/product_gallery.html', {'product': product, 'images': images})


# ─── FEAT-07: Promo Code Admin CRUD ──────────────────────────────────────────

@admin_required
def admin_promo_codes(request):
    promos = PromoCode.objects.order_by('-created')
    return render(request, 'mall/admin/promo_codes.html', {'promos': promos})


@admin_required
def admin_promo_code_add(request):
    if request.method == 'POST':
        try:
            from decimal import InvalidOperation
            code = request.POST.get('code', '').strip().upper()
            if not code:
                raise ValueError('Code is required.')
            if PromoCode.objects.filter(code=code).exists():
                raise ValueError(f'Promo code "{code}" already exists.')
            dtype  = request.POST.get('discount_type', 'percent')
            value  = Decimal(request.POST.get('discount_value', '0'))
            min_v  = Decimal(request.POST.get('min_order_value', '0'))
            max_u  = request.POST.get('max_uses', '').strip() or None
            vfrom  = request.POST.get('valid_from', '') or None
            vuntil = request.POST.get('valid_until', '') or None
            promo = PromoCode(
                code=code, discount_type=dtype, discount_value=value,
                min_order_value=min_v,
                max_uses=int(max_u) if max_u else None,
                is_active='is_active' in request.POST,
            )
            if vfrom:
                from django.utils.dateparse import parse_datetime
                promo.valid_from = parse_datetime(vfrom) or timezone.now()
            if vuntil:
                from django.utils.dateparse import parse_datetime
                promo.valid_until = parse_datetime(vuntil)
            promo.save()
            audit_log(request, 'promo_create', f'Promo "{promo.code}" (id={promo.pk})')
            messages.success(request, f'Promo code "{promo.code}" created.')
            return redirect('admin_promo_codes')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/promo_code_form.html', {'action': 'Add'})


@admin_required
def admin_promo_code_edit(request, pk):
    promo = get_object_or_404(PromoCode, pk=pk)
    if request.method == 'POST':
        try:
            from decimal import InvalidOperation
            promo.discount_type  = request.POST.get('discount_type', 'percent')
            promo.discount_value = Decimal(request.POST.get('discount_value', '0'))
            promo.min_order_value = Decimal(request.POST.get('min_order_value', '0'))
            max_u = request.POST.get('max_uses', '').strip() or None
            promo.max_uses = int(max_u) if max_u else None
            promo.is_active = 'is_active' in request.POST
            vfrom  = request.POST.get('valid_from', '') or None
            vuntil = request.POST.get('valid_until', '') or None
            if vfrom:
                from django.utils.dateparse import parse_datetime
                promo.valid_from = parse_datetime(vfrom) or promo.valid_from
            if vuntil:
                from django.utils.dateparse import parse_datetime
                promo.valid_until = parse_datetime(vuntil)
            promo.save()
            audit_log(request, 'promo_update', f'Promo "{promo.code}" (id={promo.pk})')
            messages.success(request, f'Promo code "{promo.code}" updated.')
            return redirect('admin_promo_codes')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/promo_code_form.html', {'action': 'Edit', 'promo': promo})


@admin_required
def admin_promo_code_delete(request, pk):
    promo = get_object_or_404(PromoCode, pk=pk)
    if request.method == 'POST':
        code = promo.code
        promo.delete()
        audit_log(request, 'promo_delete', f'Promo "{code}"')
        messages.success(request, f'Promo code "{code}" deleted.')
        return redirect('admin_promo_codes')
    return render(request, 'mall/admin/confirm_delete.html', {'object': promo, 'type': 'Promo Code'})


# ─── FEAT-NOTIF: Admin Notifications ─────────────────────────────────────────

@admin_required
def admin_notifications(request):
    notifs   = Notification.objects.filter(user=request.user).order_by('-created')
    unread   = notifs.filter(is_read=False).count()
    paginator = Paginator(notifs, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))
    if request.method == 'POST' and request.POST.get('action') == 'mark_all_read':
        notifs.filter(is_read=False).update(is_read=True)
        messages.success(request, 'All notifications marked as read.')
        return redirect('admin_notifications')
    return render(request, 'mall/admin/notifications.html', {
        'notifications': page_obj,
        'page_obj':      page_obj,
        'unread_count':  unread,
    })



# ─── FEAT: Feedback Admin ─────────────────────────────────────────────────────

@admin_required
def admin_feedback(request):
    from .models import OrderFeedback
    from django.db.models import Avg, Count
    feedbacks = OrderFeedback.objects.select_related('order', 'user').order_by('-created')

    stats = feedbacks.aggregate(
        avg_delivery=Avg('delivery_rating'),
        avg_packaging=Avg('packaging_rating'),
        avg_service=Avg('service_rating'),
        avg_nps=Avg('nps_score'),
        total=Count('id'),
    )
    promoters  = feedbacks.filter(nps_score__gte=9).count()
    passives   = feedbacks.filter(nps_score__gte=7, nps_score__lte=8).count()
    detractors = feedbacks.filter(nps_score__lte=6).count()
    total      = stats['total'] or 1
    nps_score  = round(((promoters - detractors) / total) * 100) if total else 0

    paginator = Paginator(feedbacks, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))
    return render(request, 'mall/admin/feedback.html', {
        'feedbacks':   page_obj,
        'page_obj':    page_obj,
        'stats':       stats,
        'promoters':   promoters,
        'passives':    passives,
        'detractors':  detractors,
        'nps_score':   nps_score,
        'total':       total,
    })


# ─── FEAT: AI Admin Insights ──────────────────────────────────────────────────

@admin_required
def admin_ai_insights(request):
    """
    AI-powered admin dashboard: generates order summaries & customer insights
    by calling the Anthropic API server-side with aggregated store data.
    """
    import json as _json
    import urllib.request

    # Aggregate data to feed to Claude
    from django.db.models import Sum, Avg, Count
    from .models import OrderFeedback

    total_orders   = Order.objects.count()
    total_revenue  = Order.objects.filter(paid=True).aggregate(t=Sum('total_price'))['t'] or 0
    status_counts  = dict(Order.objects.values_list('status').annotate(c=Count('id')))
    top_products   = list(
        OrderItem.objects.values('product__name')
        .annotate(sold=Sum('quantity'))
        .order_by('-sold')[:5]
        .values_list('product__name', 'sold')
    )
    low_stock      = list(
        Product.objects.filter(available=True, stock__lte=5)
        .values_list('name', 'stock')
        .order_by('stock')[:10]
    )
    avg_nps        = OrderFeedback.objects.aggregate(n=Avg('nps_score'))['n']
    recent_feedback = list(
        OrderFeedback.objects.order_by('-created')[:10]
        .values_list('comment', flat=True)
    )
    feedback_comments = [c for c in recent_feedback if c.strip()]

    data_summary = f"""
Market Store Snapshot:
- Total orders: {total_orders}
- Total paid revenue: GH₵{float(total_revenue):.2f}
- Orders by status: {status_counts}
- Top 5 best-selling products: {top_products}
- Low stock items (≤5 units): {low_stock}
- Average NPS score: {round(float(avg_nps), 1) if avg_nps else 'N/A'}
- Recent customer feedback comments: {feedback_comments[:5]}
"""

    prompt = f"""{data_summary}

You are a business analyst for a Ghanaian e-commerce store. Based on the snapshot above, write a concise management report with:
1. A 2-sentence overall performance summary
2. Top 3 actionable recommendations for the store owner
3. Any urgent issues to address (stock, cancellations, negative feedback)

Write in plain business English, be specific to the data, keep it under 300 words."""

    ai_report = None
    ai_error  = None
    if request.method == 'POST' and request.POST.get('action') == 'generate':
        try:
            payload = _json.dumps({
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 600,
                'messages': [{'role': 'user', 'content': prompt}],
            }).encode()
            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'anthropic-version': '2023-06-01',
                },
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = _json.loads(resp.read())
                ai_report = data['content'][0]['text'].strip()
        except Exception as e:
            ai_error = f'Could not generate AI report: {e}'

    return render(request, 'mall/admin/ai_insights.html', {
        'ai_report':     ai_report,
        'ai_error':      ai_error,
        'total_orders':  total_orders,
        'total_revenue': float(total_revenue),
        'status_counts': status_counts,
        'top_products':  top_products,
        'low_stock':     low_stock,
        'avg_nps':       round(float(avg_nps), 1) if avg_nps else None,
    })


# ─── CSV Import ───────────────────────────────────────────────────────────────

import csv
import io
from django.utils.text import slugify
from django.db import transaction as _tx

# Maximum rows we'll process in a single upload (protects against huge files)
CSV_ROW_LIMIT = 1000
CSV_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


def _parse_csv(file_obj):
    """Read an uploaded file and return (header_list, row_dicts). Raises ValueError on bad input."""
    raw = file_obj.read(CSV_MAX_BYTES + 1)
    if len(raw) > CSV_MAX_BYTES:
        raise ValueError('File is too large. Maximum size is 2 MB.')
    try:
        text = raw.decode('utf-8-sig')   # utf-8-sig strips the BOM that Excel adds
    except UnicodeDecodeError:
        raise ValueError('File must be saved as UTF-8. Please re-save and try again.')
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValueError('The file appears to be empty.')
    if len(rows) > CSV_ROW_LIMIT:
        raise ValueError(f'Too many rows. Maximum is {CSV_ROW_LIMIT} per upload.')
    return reader.fieldnames or [], rows


def _req(row, key, label):
    """Return stripped value or raise ValueError if blank."""
    val = row.get(key, '').strip()
    if not val:
        raise ValueError(f'"{label}" is required.')
    return val


def _opt(row, key, default=''):
    return row.get(key, default).strip()


@admin_required
def admin_csv_import(request):
    """
    Landing page — lets the admin choose which entity to import and shows
    sample CSV format for each type.
    """
    return render(request, 'mall/admin/csv_import.html')


@admin_required
def admin_csv_import_products(request):
    """
    Import products from CSV.
    Required columns: name, category, price, stock
    Optional columns: slug, description, available (yes/no/1/0)
    Category is matched by name (case-insensitive); created if missing.
    Duplicate slugs are skipped with a warning.
    """
    template = 'mall/admin/csv_import_products.html'
    results = None

    if request.method == 'POST':
        f = request.FILES.get('csv_file')
        if not f:
            messages.error(request, 'Please choose a CSV file to upload.')
            return render(request, template, {'results': results})

        created = skipped = errors = 0
        error_rows = []

        try:
            _, rows = _parse_csv(f)
        except ValueError as e:
            messages.error(request, str(e))
            return render(request, template, {'results': results})

        cat_cache = {}  # name_lower → Category instance

        for i, row in enumerate(rows, start=2):   # row 1 = header
            try:
                name  = _req(row, 'name', 'name')
                cat_name = _req(row, 'category', 'category')
                price = _req(row, 'price', 'price')
                stock_raw = _req(row, 'stock', 'stock')

                try:
                    price = Decimal(price)
                    if price <= 0:
                        raise ValueError()
                except Exception:
                    raise ValueError(f'price "{price}" is not a valid positive number.')

                try:
                    stock = int(stock_raw)
                    if stock < 0:
                        raise ValueError()
                except Exception:
                    raise ValueError(f'stock "{stock_raw}" must be a non-negative integer.')

                avail_raw = _opt(row, 'available', 'yes').lower()
                available = avail_raw not in ('0', 'no', 'false', 'n')

                desc = _opt(row, 'description', '')

                # Auto-generate slug from name if not provided
                slug = _opt(row, 'slug') or slugify(name)
                if not slug:
                    raise ValueError('Could not generate a slug from the product name.')

                if Product.objects.filter(slug=slug).exists():
                    skipped += 1
                    error_rows.append({'row': i, 'name': name, 'reason': f'Slug "{slug}" already exists — skipped.'})
                    continue

                # Get or create category
                ck = cat_name.lower()
                if ck not in cat_cache:
                    cat_obj, _ = Category.objects.get_or_create(
                        name__iexact=cat_name,
                        defaults={'name': cat_name, 'slug': slugify(cat_name) or f'cat-{cat_name[:20]}'},
                    )
                    cat_cache[ck] = cat_obj
                cat = cat_cache[ck]

                Product.objects.create(
                    name=name, slug=slug, description=desc,
                    price=price, stock=stock, category=cat,
                    available=available,
                )
                created += 1

            except ValueError as e:
                errors += 1
                error_rows.append({'row': i, 'name': row.get('name', '?'), 'reason': str(e)})

        results = {'created': created, 'skipped': skipped, 'errors': errors, 'error_rows': error_rows}
        if created:
            audit_log(request, 'csv_import', f'Products CSV', f'{created} created, {skipped} skipped, {errors} errors')
            messages.success(request, f'Import complete: {created} product(s) created.')
        if skipped:
            messages.warning(request, f'{skipped} row(s) skipped (duplicate slugs).')
        if errors:
            messages.error(request, f'{errors} row(s) had errors — see details below.')

    return render(request, template, {'results': results})


@admin_required
def admin_csv_import_branches(request):
    """
    Import branches from CSV.
    Required columns: name, address, city, region
    Optional: branch_type, phone, email, opening_hours, landmark, latitude, longitude, is_active
    region must match one of the REGION_CHOICES keys (e.g. greater_accra, ashanti).
    """
    from .models import REGION_CHOICES as _RC
    valid_regions = {k for k, _ in _RC}
    valid_types   = {'main', 'express', 'agent'}
    template = 'mall/admin/csv_import_branches.html'
    results  = None

    if request.method == 'POST':
        f = request.FILES.get('csv_file')
        if not f:
            messages.error(request, 'Please choose a CSV file to upload.')
            return render(request, template, {'results': results})

        created = skipped = errors = 0
        error_rows = []

        try:
            _, rows = _parse_csv(f)
        except ValueError as e:
            messages.error(request, str(e))
            return render(request, template, {'results': results})

        for i, row in enumerate(rows, start=2):
            try:
                name    = _req(row, 'name', 'name')
                address = _req(row, 'address', 'address')
                city    = _req(row, 'city', 'city')
                region  = _req(row, 'region', 'region').lower().replace(' ', '_')

                if region not in valid_regions:
                    raise ValueError(f'region "{region}" is not a valid Ghana region key.')

                branch_type = _opt(row, 'branch_type', 'main').lower()
                if branch_type not in valid_types:
                    branch_type = 'main'

                # Check for duplicate (same name + city)
                if Branch.objects.filter(name__iexact=name, city__iexact=city).exists():
                    skipped += 1
                    error_rows.append({'row': i, 'name': name, 'reason': f'Branch "{name}" in {city} already exists — skipped.'})
                    continue

                lat = _opt(row, 'latitude')
                lng = _opt(row, 'longitude')
                try:
                    lat = float(lat) if lat else None
                    lng = float(lng) if lng else None
                except ValueError:
                    lat = lng = None

                active_raw = _opt(row, 'is_active', 'yes').lower()
                is_active  = active_raw not in ('0', 'no', 'false', 'n')

                Branch.objects.create(
                    name=name, address=address, city=city,
                    region=region, branch_type=branch_type,
                    phone=_opt(row, 'phone'),
                    email=_opt(row, 'email'),
                    opening_hours=_opt(row, 'opening_hours', 'Mon–Sat: 8am – 8pm | Sun: 10am – 6pm'),
                    landmark=_opt(row, 'landmark'),
                    latitude=lat, longitude=lng,
                    is_active=is_active,
                )
                created += 1

            except ValueError as e:
                errors += 1
                error_rows.append({'row': i, 'name': row.get('name', '?'), 'reason': str(e)})

        results = {'created': created, 'skipped': skipped, 'errors': errors, 'error_rows': error_rows}
        if created:
            audit_log(request, 'csv_import', f'Branches CSV', f'{created} created, {skipped} skipped, {errors} errors')
            messages.success(request, f'Import complete: {created} branch(es) created.')
        if skipped:
            messages.warning(request, f'{skipped} row(s) skipped (duplicates).')
        if errors:
            messages.error(request, f'{errors} row(s) had errors — see details below.')

    return render(request, template, {'results': results})


@admin_required
def admin_csv_import_promo_codes(request):
    """
    Import promo codes from CSV.
    Required columns: code, discount_type (percent/fixed), discount_value
    Optional: min_order_value, max_uses, valid_from, valid_until, is_active
    Dates should be in YYYY-MM-DD or YYYY-MM-DD HH:MM format.
    Duplicate codes are skipped.
    """
    from django.utils.dateparse import parse_datetime, parse_date
    from django.utils.timezone import make_aware
    import datetime

    template = 'mall/admin/csv_import_promo_codes.html'
    results  = None

    if request.method == 'POST':
        f = request.FILES.get('csv_file')
        if not f:
            messages.error(request, 'Please choose a CSV file to upload.')
            return render(request, template, {'results': results})

        created = skipped = errors = 0
        error_rows = []

        try:
            _, rows = _parse_csv(f)
        except ValueError as e:
            messages.error(request, str(e))
            return render(request, template, {'results': results})

        def _parse_dt(raw):
            """Parse a date or datetime string to an aware datetime, or None."""
            if not raw:
                return None
            dt = parse_datetime(raw)
            if dt:
                return make_aware(dt) if dt.tzinfo is None else dt
            d = parse_date(raw)
            if d:
                return make_aware(datetime.datetime(d.year, d.month, d.day))
            raise ValueError(f'Cannot parse date "{raw}". Use YYYY-MM-DD or YYYY-MM-DD HH:MM.')

        for i, row in enumerate(rows, start=2):
            try:
                code = _req(row, 'code', 'code').upper()
                dtype = _req(row, 'discount_type', 'discount_type').lower()
                if dtype not in ('percent', 'fixed'):
                    raise ValueError(f'discount_type must be "percent" or "fixed", got "{dtype}".')

                try:
                    dvalue = Decimal(_req(row, 'discount_value', 'discount_value'))
                    if dvalue < 0:
                        raise ValueError()
                    if dtype == 'percent' and dvalue > 100:
                        raise ValueError('Percentage discount cannot exceed 100.')
                except (InvalidOperation, ValueError) as e:
                    raise ValueError(f'discount_value: {e}')

                min_val_raw = _opt(row, 'min_order_value', '0')
                try:
                    min_val = Decimal(min_val_raw) if min_val_raw else Decimal('0')
                except InvalidOperation:
                    min_val = Decimal('0')

                max_uses_raw = _opt(row, 'max_uses', '')
                max_uses = None
                if max_uses_raw:
                    try:
                        max_uses = int(max_uses_raw)
                        if max_uses < 0:
                            raise ValueError()
                    except ValueError:
                        raise ValueError(f'max_uses "{max_uses_raw}" must be a positive integer or blank.')

                valid_from  = _parse_dt(_opt(row, 'valid_from'))
                valid_until = _parse_dt(_opt(row, 'valid_until'))

                active_raw = _opt(row, 'is_active', 'yes').lower()
                is_active  = active_raw not in ('0', 'no', 'false', 'n')

                if PromoCode.objects.filter(code=code).exists():
                    skipped += 1
                    error_rows.append({'row': i, 'name': code, 'reason': f'Code "{code}" already exists — skipped.'})
                    continue

                PromoCode.objects.create(
                    code=code,
                    discount_type=dtype,
                    discount_value=dvalue,
                    min_order_value=min_val,
                    max_uses=max_uses,
                    valid_from=valid_from or timezone.now(),
                    valid_until=valid_until,
                    is_active=is_active,
                )
                created += 1

            except ValueError as e:
                errors += 1
                error_rows.append({'row': i, 'name': row.get('code', '?'), 'reason': str(e)})

        results = {'created': created, 'skipped': skipped, 'errors': errors, 'error_rows': error_rows}
        if created:
            audit_log(request, 'csv_import', f'Promo Codes CSV', f'{created} created, {skipped} skipped, {errors} errors')
            messages.success(request, f'Import complete: {created} promo code(s) created.')
        if skipped:
            messages.warning(request, f'{skipped} row(s) skipped (duplicates).')
        if errors:
            messages.error(request, f'{errors} row(s) had errors — see details below.')

    return render(request, template, {'results': results})


@admin_required
def admin_csv_template_download(request, entity):
    """
    Serve a sample CSV template for download so admins know exactly
    what columns and format to use.
    """
    import csv as _csv
    from django.http import HttpResponse

    templates = {
        'products': {
            'filename': 'products_template.csv',
            'headers':  ['name', 'category', 'price', 'stock', 'slug', 'description', 'available'],
            'sample':   [['Shea Butter 250ml', 'Skincare', '25.00', '100', 'shea-butter-250ml', 'Pure raw shea butter', 'yes'],
                         ['Honey 500g Jar', 'Food & Honey', '45.00', '50', '', 'Natural wildflower honey', 'yes']],
        },
        'branches': {
            'filename': 'branches_template.csv',
            'headers':  ['name', 'address', 'city', 'region', 'branch_type', 'phone', 'email',
                         'opening_hours', 'landmark', 'latitude', 'longitude', 'is_active'],
            'sample':   [['Accra Main', '12 High Street', 'Accra', 'greater_accra', 'main',
                          '0244000000', 'accra@market.com', 'Mon–Sat: 8am–8pm', 'Near Accra Mall', '5.6037', '-0.1870', 'yes'],
                         ['Kumasi Express', '5 Adum Road', 'Kumasi', 'ashanti', 'express',
                          '0244111111', '', '', 'Opp. Kejetia Market', '6.6885', '-1.6244', 'yes']],
        },
        'promo_codes': {
            'filename': 'promo_codes_template.csv',
            'headers':  ['code', 'discount_type', 'discount_value', 'min_order_value',
                         'max_uses', 'valid_from', 'valid_until', 'is_active'],
            'sample':   [['WELCOME10', 'percent', '10', '0', '500', '2025-01-01', '2025-12-31', 'yes'],
                         ['SAVE5GHC', 'fixed', '5.00', '50', '', '2025-06-01', '', 'yes']],
        },
    }

    if entity not in templates:
        from django.http import Http404
        raise Http404

    tmpl = templates[entity]
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{tmpl["filename"]}"'
    writer = _csv.writer(response)
    writer.writerow(tmpl['headers'])
    for row in tmpl['sample']:
        writer.writerow(row)
    return response


# ─── Audit Log View ──────────────────────────────────────────────────────────

@admin_required
def admin_audit_log(request):
    """
    Paginated, filterable audit log for staff and site owners.
    Superusers see everything; regular staff see only their own entries.
    Supports filtering by action category, actor username, and date range.
    Provides a CSV export of the filtered results.
    """
    logs = AuditLog.objects.select_related('actor')

    # Superusers see all logs; staff see only their own
    if not request.user.is_superuser:
        logs = logs.filter(actor=request.user)

    # ── Filters ──────────────────────────────────────────────────────────────
    category   = request.GET.get('category', '').strip()
    actor_q    = request.GET.get('actor', '').strip()
    action_q   = request.GET.get('action', '').strip()
    date_from  = request.GET.get('date_from', '').strip()
    date_to    = request.GET.get('date_to', '').strip()
    export     = request.GET.get('export', '')

    if category:
        # Filter by category prefix (product, order, user, etc.)
        from django.db.models import Q as _Q
        matching_actions = [a for a, _ in AuditLog.ACTION_CHOICES
                            if AuditLog.ACTION_CATEGORY.get(a) == category]
        logs = logs.filter(action__in=matching_actions)

    if action_q:
        logs = logs.filter(action=action_q)

    if actor_q and request.user.is_superuser:
        logs = logs.filter(actor__username__icontains=actor_q)

    if date_from:
        from django.utils.dateparse import parse_date
        d = parse_date(date_from)
        if d:
            logs = logs.filter(timestamp__date__gte=d)

    if date_to:
        from django.utils.dateparse import parse_date
        d = parse_date(date_to)
        if d:
            logs = logs.filter(timestamp__date__lte=d)

    # ── CSV Export ────────────────────────────────────────────────────────────
    if export == 'csv':
        import csv as _csv
        from django.http import HttpResponse as _HR
        resp = _HR(content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="audit_log.csv"'
        w = _csv.writer(resp)
        w.writerow(['Timestamp', 'Actor', 'IP Address', 'Action', 'Target', 'Detail'])
        for entry in logs:
            w.writerow([
                entry.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                entry.actor.username if entry.actor else '—',
                entry.ip_address or '—',
                entry.get_action_display(),
                entry.target_repr,
                entry.detail,
            ])
        return resp

    # ── Pagination ────────────────────────────────────────────────────────────
    paginator = Paginator(logs, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))

    # Build category list for filter dropdown
    categories = sorted(set(AuditLog.ACTION_CATEGORY.values()))

    return render(request, 'mall/admin/audit_log.html', {
        'logs':        page_obj,
        'page_obj':    page_obj,
        'categories':  categories,
        'all_actions': AuditLog.ACTION_CHOICES,
        'category':    category,
        'actor_q':     actor_q,
        'action_q':    action_q,
        'date_from':   date_from,
        'date_to':     date_to,
        'is_superuser': request.user.is_superuser,
        'total':       logs.count(),
    })


# ─── Admin 2FA Setup / Verify ────────────────────────────────────────────────

@admin_required
def admin_2fa_setup(request):
    """
    Let a staff member enable TOTP 2FA on their account.
    Shows a QR code they scan with their authenticator app, then confirms
    with a live token before enabling.
    """
    import pyotp, qrcode, io, base64
    totp_obj = AdminTOTP.get_or_create_secret(request.user)
    error    = None
    success  = False

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'enable':
            token = request.POST.get('token', '').strip()
            if totp_obj.verify(token):
                totp_obj.is_enabled = True
                totp_obj.save()
                audit_log(request, 'admin_login', f'2FA enabled for {request.user.username}')
                messages.success(request, '2FA enabled successfully.')
                return redirect('admin_dashboard')
            else:
                error = 'Invalid code. Please try again.'
        elif action == 'disable' and request.user.is_superuser:
            totp_obj.is_enabled = False
            totp_obj.save()
            audit_log(request, 'admin_login', f'2FA disabled for {request.user.username}')
            messages.success(request, '2FA disabled.')
            return redirect('admin_dashboard')

    # Build QR code URI
    uri = pyotp.TOTP(totp_obj.secret).provisioning_uri(
        name=request.user.email or request.user.username,
        issuer_name='Honey Cave Market',
    )
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return render(request, 'mall/admin/2fa_setup.html', {
        'totp':   totp_obj,
        'qr_b64': qr_b64,
        'error':  error,
    })


def admin_2fa_verify(request):
    """
    Intermediate page shown after password login if 2FA is enabled.
    Session key 'needs_2fa' is set by admin_login_view on success.
    """
    if not request.session.get('needs_2fa_user_id'):
        return redirect('admin_login')

    error = None
    if request.method == 'POST':
        from django.contrib.auth import login as _login
        user_id = request.session.get('needs_2fa_user_id')
        token   = request.POST.get('token', '').strip()
        try:
            user    = User.objects.get(pk=user_id, is_staff=True)
            totp    = user.totp
            if totp.is_enabled and totp.verify(token):
                del request.session['needs_2fa_user_id']
                _login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                request.session.cycle_key()
                audit_log(request, 'admin_login', f'2FA verified for {user.username}')
                messages.success(request, f'Welcome, {user.first_name or user.username}.')
                return redirect('admin_dashboard')
            else:
                error = 'Invalid code. Please try again.'
        except (User.DoesNotExist, AdminTOTP.DoesNotExist):
            error = 'Session expired. Please log in again.'
            return redirect('admin_login')

    return render(request, 'mall/admin/2fa_verify.html', {'error': error})


# ─── Inventory Alerts Dashboard ───────────────────────────────────────────────

@admin_required
def admin_inventory(request):
    """
    Low-stock dashboard: lists products below threshold with bulk restock form.
    POST with product IDs + new quantities to update stock in one shot.
    """
    threshold = int(request.GET.get('threshold', 10))

    if request.method == 'POST':
        updated = 0
        for key, val in request.POST.items():
            if key.startswith('stock_'):
                try:
                    pk  = int(key.split('_')[1])
                    qty = int(val)
                    if qty < 0:
                        continue
                    rows = Product.objects.filter(pk=pk).update(stock=qty)
                    if rows:
                        updated += 1
                        p = Product.objects.get(pk=pk)
                        audit_log(request, 'product_update', f'Product "{p.name}" (id={pk})',
                                  f'stock restocked to {qty}')
                except (ValueError, TypeError, Product.DoesNotExist):
                    pass
        if updated:
            messages.success(request, f'{updated} product(s) restocked.')
        return redirect(f'{request.path}?threshold={threshold}')

    low_stock = (Product.objects
                 .filter(stock__lte=threshold)
                 .select_related('category')
                 .order_by('stock'))

    out_of_stock  = low_stock.filter(stock=0).count()
    critical      = low_stock.filter(stock__gt=0, stock__lte=5).count()
    warning       = low_stock.filter(stock__gt=5, stock__lte=threshold).count()

    return render(request, 'mall/admin/inventory.html', {
        'low_stock':     low_stock,
        'threshold':     threshold,
        'out_of_stock':  out_of_stock,
        'critical':      critical,
        'warning':       warning,
        'total_low':     low_stock.count(),
    })


# ─── Promotions (internal banners) ────────────────────────────────────────────

@admin_required
def admin_promotions(request):
    """List all promotions with live/scheduled/expired status + click stats."""
    placement = request.GET.get('placement', '')
    promos = Promotion.objects.all().order_by('placement', '-priority', '-created')
    if placement:
        promos = promos.filter(placement=placement)
    paginator = Paginator(promos, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))
    return render(request, 'mall/admin/promotions.html', {
        'promotions':        page_obj,
        'page_obj':          page_obj,
        'placement_filter':  placement,
        'placement_choices': Promotion.PLACEMENT_CHOICES,
    })


def _apply_promotion_fields(request, promo):
    """Shared form-processing for add + edit. Raises ValueError on bad input."""
    promo.title      = _clean_str(request.POST, 'title',    120)
    promo.subtitle   = _clean_str(request.POST, 'subtitle', 240, required=False)
    promo.link_url   = _clean_str(request.POST, 'link_url', 500, required=False)
    promo.cta_text   = _clean_str(request.POST, 'cta_text',  40, required=False) or 'Shop Now'
    promo.placement  = _clean_str(request.POST, 'placement', 20)
    valid_placements = {k for k, _ in Promotion.PLACEMENT_CHOICES}
    if promo.placement not in valid_placements:
        raise ValueError('Invalid placement choice.')
    try:
        promo.priority = int(request.POST.get('priority', 0) or 0)
    except ValueError:
        promo.priority = 0
    promo.bg_color   = _clean_str(request.POST, 'bg_color',   20, required=False)
    promo.text_color = _clean_str(request.POST, 'text_color', 20, required=False)
    promo.is_active  = 'is_active' in request.POST

    # Optional datetime fields — accept local datetime-local strings or empty
    def _parse_dt(key):
        raw = (request.POST.get(key) or '').strip()
        if not raw:
            return None
        from django.utils.dateparse import parse_datetime
        val = parse_datetime(raw)
        if val is None:
            raise ValueError(f'"{key}" is not a valid date/time.')
        if timezone.is_naive(val):
            val = timezone.make_aware(val, timezone.get_current_timezone())
        return val
    promo.starts_at = _parse_dt('starts_at')
    promo.ends_at   = _parse_dt('ends_at')

    # Image upload (uses the same validator products use)
    if request.FILES.get('image'):
        err = validate_uploaded_image(request.FILES['image'])
        if err:
            raise ValueError(err)
        promo.image = request.FILES['image']


@admin_required
def admin_promotion_add(request):
    if request.method == 'POST':
        try:
            promo = Promotion()
            _apply_promotion_fields(request, promo)
            promo.save()
            audit_log(request, 'promotion_create', f'Promotion "{promo.title}" (id={promo.pk})')
            messages.success(request, f'Promotion "{promo.title}" created.')
            return redirect('admin_promotions')
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/promotion_form.html', {
        'action':            'Add',
        'placement_choices': Promotion.PLACEMENT_CHOICES,
    })


@admin_required
def admin_promotion_edit(request, pk):
    promo = get_object_or_404(Promotion, pk=pk)
    if request.method == 'POST':
        try:
            _apply_promotion_fields(request, promo)
            promo.save()
            audit_log(request, 'promotion_update', f'Promotion "{promo.title}" (id={promo.pk})')
            messages.success(request, f'Promotion "{promo.title}" updated.')
            return redirect('admin_promotions')
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, 'mall/admin/promotion_form.html', {
        'action':            'Edit',
        'promotion':         promo,
        'placement_choices': Promotion.PLACEMENT_CHOICES,
    })


@admin_required
def admin_promotion_delete(request, pk):
    promo = get_object_or_404(Promotion, pk=pk)
    if request.method == 'POST':
        title = promo.title
        promo.delete()
        audit_log(request, 'promotion_delete', f'Promotion "{title}" (id={pk})')
        messages.success(request, f'Promotion "{title}" deleted.')
        return redirect('admin_promotions')
    return render(request, 'mall/admin/confirm_delete.html', {
        'object':     promo,
        'type':       'promotion',
        'cancel_url': '/panel/promotions/',
    })


@admin_required
def admin_promotion_toggle(request, pk):
    """Quick on/off without going through the full edit form."""
    promo = get_object_or_404(Promotion, pk=pk)
    if request.method == 'POST':
        promo.is_active = not promo.is_active
        promo.save(update_fields=['is_active'])
        audit_log(request, 'promotion_toggle',
                  f'Promotion "{promo.title}" (id={promo.pk})',
                  f'is_active={promo.is_active}')
        messages.success(request, f'"{promo.title}" is now {"active" if promo.is_active else "paused"}.')
    return redirect('admin_promotions')


# ─── Site Settings (contact info, socials) ────────────────────────────────────

@admin_required
def admin_site_settings(request):
    """Edit the single SiteSettings row — contact info, hours, social links."""
    settings_obj = SiteSettings.load()
    if request.method == 'POST':
        try:
            settings_obj.phone_primary   = _clean_str(request.POST, 'phone_primary',   40,  required=False)
            settings_obj.phone_secondary = _clean_str(request.POST, 'phone_secondary', 40,  required=False)
            settings_obj.email           = _clean_str(request.POST, 'email',           254, required=False)
            settings_obj.whatsapp        = _clean_str(request.POST, 'whatsapp',        40,  required=False)
            settings_obj.hours_weekday   = _clean_str(request.POST, 'hours_weekday',   100, required=False)
            settings_obj.hours_sunday    = _clean_str(request.POST, 'hours_sunday',    100, required=False)
            settings_obj.head_office     = _clean_str(request.POST, 'head_office',     200, required=False)

            # Brand asset uploads — replace existing file or clear it.
            for field_name in ('logo', 'og_image'):
                if request.FILES.get(field_name):
                    err = validate_uploaded_image(request.FILES[field_name])
                    if err:
                        raise ValueError(f'{field_name}: {err}')
                    setattr(settings_obj, field_name, request.FILES[field_name])
                elif request.POST.get(f'{field_name}_clear') == '1':
                    # User ticked the "Clear" checkbox to remove the upload
                    # and revert to the static file fallback.
                    existing = getattr(settings_obj, field_name)
                    if existing:
                        existing.delete(save=False)
                    setattr(settings_obj, field_name, None)

            # Social URLs: empty strings are fine, but if a user enters
            # "facebook.com/foo" without the scheme, Django's URLField will
            # reject the save with a ValidationError. Auto-prepend https:// so
            # the form is forgiving instead of blowing up.
            def _url(key):
                v = _clean_str(request.POST, key, 500, required=False)
                if v and not v.lower().startswith(('http://', 'https://')):
                    v = 'https://' + v
                return v
            settings_obj.facebook_url    = _url('facebook_url')
            settings_obj.instagram_url   = _url('instagram_url')
            settings_obj.twitter_url     = _url('twitter_url')
            settings_obj.tiktok_url      = _url('tiktok_url')

            # Maintenance mode
            settings_obj.maintenance_mode    = 'maintenance_mode' in request.POST
            settings_obj.maintenance_message = _clean_str(request.POST, 'maintenance_message', 500, required=False)
            settings_obj.maintenance_bypass_token = _clean_str(request.POST, 'maintenance_bypass_token', 80, required=False)

            # Messaging via Nalo (SMS + OTP) — primary provider
            settings_obj.nalo_enabled     = 'nalo_enabled' in request.POST
            settings_obj.nalo_username    = _clean_str(request.POST, 'nalo_username', 150, required=False)
            # Only overwrite the password when a new value is supplied, so an
            # admin saving the form doesn't accidentally wipe a stored secret by
            # leaving the (password) field blank.
            _new_nalo_pass = _clean_str(request.POST, 'nalo_password', 300, required=False)
            if _new_nalo_pass:
                settings_obj.nalo_password = _new_nalo_pass
            settings_obj.nalo_sender_id   = _clean_str(request.POST, 'nalo_sender_id', 11, required=False)
            settings_obj.nalo_api_url     = (_clean_str(request.POST, 'nalo_api_url', 300, required=False)
                                             or 'https://sms.nalosolutions.com/smsbackend/clientapi/Resl_Nalo/send-message/')

            # Messaging via Tiliow (WhatsApp + SMS + OTP) — secondary provider
            settings_obj.tiliow_enabled     = 'tiliow_enabled' in request.POST
            # Only overwrite the API key when a new value is supplied, so an
            # admin saving the form doesn't accidentally wipe a stored key by
            # leaving the (password) field blank.
            _new_key = _clean_str(request.POST, 'tiliow_api_key', 300, required=False)
            if _new_key:
                settings_obj.tiliow_api_key = _new_key
            settings_obj.tiliow_api_url     = (_clean_str(request.POST, 'tiliow_api_url', 300, required=False)
                                               or 'https://api.tiliow.com/v1/messages')
            settings_obj.tiliow_sender_id   = _clean_str(request.POST, 'tiliow_sender_id', 80, required=False)

            # Notification audience toggles + admin alert number (shared with messaging)
            settings_obj.wa_notify_customer = 'wa_notify_customer' in request.POST
            settings_obj.wa_notify_admin    = 'wa_notify_admin' in request.POST
            settings_obj.wa_admin_number    = _clean_str(request.POST, 'wa_admin_number', 40, required=False)

            # full_clean() runs Django's field validators (URL format, email
            # format, max_length) BEFORE the database write, so we catch any
            # bad input here as a friendly message instead of a 500 page.
            settings_obj.full_clean()
            settings_obj.save()
            audit_log(request, 'site_settings_update', 'Site Settings',
                      f'Contact info updated by {request.user.username}')

            # Bust the maintenance middleware in-process cache immediately
            from .middleware import MaintenanceModeMiddleware
            MaintenanceModeMiddleware._cache_ts = 0

            mode_status = 'ON — site is in maintenance mode' if settings_obj.maintenance_mode else 'OFF — site is live'
            messages.success(request, f'Site settings saved. Maintenance mode: {mode_status}.')
            return redirect('admin_site_settings')
        except ValueError as e:
            messages.error(request, str(e))
        except ValidationError as e:
            # Django collects per-field errors in e.message_dict
            errs = []
            try:
                for field, msgs in e.message_dict.items():
                    errs.append(f'{field}: {"; ".join(msgs)}')
            except Exception:
                errs.append(str(e))
            messages.error(request, 'Could not save: ' + ' | '.join(errs))
        except Exception as e:
            messages.error(request, f'Unexpected error saving site settings: {e}')
    return render(request, 'mall/admin/site_settings.html', {
        'settings': settings_obj,
    })


# ════════════════════════════════════════════════════════════════════════════
# Rider Roster — admin manages the persistent list of riders.
# Riders are created here by admin OR auto-drafted (is_verified=False) when
# an officer dispatches an ad-hoc one. Either way, admin reviews them here.
# ════════════════════════════════════════════════════════════════════════════

@admin_required
def admin_riders(request):
    """List all riders with quick stats."""
    qs = (Rider.objects
          .annotate(
              n_total=Count('deliveries'),
              n_done=Count('deliveries', filter=Q(deliveries__delivered_at__isnull=False)),
          )
          .prefetch_related('branches')
          .order_by('-is_verified', '-created_at'))

    # Filters
    status = request.GET.get('status') or ''
    if status == 'unverified':
        qs = qs.filter(is_verified=False, is_active=True)
    elif status == 'verified':
        qs = qs.filter(is_verified=True, is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)

    branch_id = request.GET.get('branch') or ''
    if branch_id:
        qs = qs.filter(branches__id=branch_id)

    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))

    counts = {
        'all':        Rider.objects.count(),
        'verified':   Rider.objects.filter(is_verified=True, is_active=True).count(),
        'unverified': Rider.objects.filter(is_verified=False, is_active=True).count(),
        'inactive':   Rider.objects.filter(is_active=False).count(),
    }

    return render(request, 'mall/admin/riders.html', {
        'riders':       qs,
        'counts':       counts,
        'branches':     Branch.objects.filter(is_active=True).order_by('name'),
        'status':       status,
        'branch_id':    branch_id,
        'q':            q,
    })


@admin_required
def admin_rider_form(request, pk=None):
    """Add a new rider OR edit an existing one."""
    rider = get_object_or_404(Rider, pk=pk) if pk else None
    branches = Branch.objects.filter(is_active=True).order_by('region', 'name')

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        phone = (request.POST.get('phone') or '').strip()
        alt_phone = (request.POST.get('alt_phone') or '').strip()
        vehicle_type = request.POST.get('vehicle_type') or 'motorcycle'
        license_number = (request.POST.get('license_number') or '').strip()
        notes = (request.POST.get('notes') or '').strip()
        is_active = request.POST.get('is_active') == 'on'
        is_verified = request.POST.get('is_verified') == 'on'
        branch_ids = request.POST.getlist('branch_ids')

        # Validation
        if not name or not phone:
            messages.error(request, 'Name and phone are required.')
            return render(request, 'mall/admin/rider_form.html', {
                'rider': rider, 'branches': branches, 'action': 'Edit' if rider else 'Add',
                'vehicle_choices': Rider.VEHICLE_CHOICES,
            })

        normalized_phone = normalize_phone(phone)
        if not normalized_phone:
            messages.error(request, 'Please enter a valid phone number.')
            return render(request, 'mall/admin/rider_form.html', {
                'rider': rider, 'branches': branches, 'action': 'Edit' if rider else 'Add',
                'vehicle_choices': Rider.VEHICLE_CHOICES,
            })

        # Phone uniqueness — exclude self when editing
        existing_qs = Rider.objects.filter(phone=normalized_phone)
        if rider:
            existing_qs = existing_qs.exclude(pk=rider.pk)
        if existing_qs.exists():
            messages.error(request, f'Another rider already uses phone {phone}.')
            return render(request, 'mall/admin/rider_form.html', {
                'rider': rider, 'branches': branches, 'action': 'Edit' if rider else 'Add',
                'vehicle_choices': Rider.VEHICLE_CHOICES,
            })

        if rider is None:
            rider = Rider(created_by=request.user)
            action = 'created'
        else:
            action = 'updated'

        rider.name = name
        rider.phone = phone
        rider.alt_phone = alt_phone
        rider.vehicle_type = vehicle_type
        rider.license_number = license_number
        rider.notes = notes
        rider.is_active = is_active
        rider.is_verified = is_verified
        if 'photo' in request.FILES:
            rider.photo = request.FILES['photo']
        rider.save()
        rider.branches.set(branch_ids)

        audit_log(request, f'rider_{action}', f'Rider "{rider.name}" ({rider.phone})')
        messages.success(request, f'Rider {rider.name} {action}.')
        return redirect('admin_riders')

    return render(request, 'mall/admin/rider_form.html', {
        'rider':    rider,
        'branches': branches,
        'action':   'Edit' if rider else 'Add',
        'vehicle_choices': Rider.VEHICLE_CHOICES,
    })


@admin_required
@require_POST
def admin_rider_verify(request, pk):
    """Quick action: mark a rider as verified (one-click from list)."""
    rider = get_object_or_404(Rider, pk=pk)
    rider.is_verified = True
    rider.save(update_fields=['is_verified'])
    audit_log(request, 'rider_verify', f'Rider "{rider.name}" verified')
    messages.success(request, f'Rider {rider.name} marked as verified.')
    return redirect('admin_riders')


@admin_required
@require_POST
def admin_rider_deactivate(request, pk):
    """Quick action: deactivate a rider (preserves history; hides from dropdowns)."""
    rider = get_object_or_404(Rider, pk=pk)
    rider.is_active = False
    rider.save(update_fields=['is_active'])
    audit_log(request, 'rider_deactivate', f'Rider "{rider.name}" deactivated')
    messages.success(request, f'Rider {rider.name} deactivated.')
    return redirect('admin_riders')


@admin_required
@require_POST
def admin_rider_reactivate(request, pk):
    """Re-activate a previously deactivated rider."""
    rider = get_object_or_404(Rider, pk=pk)
    rider.is_active = True
    rider.save(update_fields=['is_active'])
    audit_log(request, 'rider_reactivate', f'Rider "{rider.name}" reactivated')
    messages.success(request, f'Rider {rider.name} reactivated.')
    return redirect('admin_riders')


# ════════════════════════════════════════════════════════════════════════════
# Branch Assignment Requests — officer requests, admin approves/rejects.
# ════════════════════════════════════════════════════════════════════════════

@admin_required
def admin_branch_requests(request):
    """List branch assignment requests with filters."""
    qs = (BranchAssignment.objects
          .select_related('officer', 'branch', 'requested_by', 'decided_by')
          .order_by('status', '-requested_at'))

    status_filter = request.GET.get('status') or 'pending'
    if status_filter != 'all':
        qs = qs.filter(status=status_filter)

    counts = {
        'pending':  BranchAssignment.objects.filter(status='pending').count(),
        'approved': BranchAssignment.objects.filter(status='approved').count(),
        'rejected': BranchAssignment.objects.filter(status='rejected').count(),
        'revoked':  BranchAssignment.objects.filter(status='revoked').count(),
    }

    return render(request, 'mall/admin/branch_requests.html', {
        'assignments':   qs,
        'counts':        counts,
        'status_filter': status_filter,
    })


@admin_required
@require_POST
def admin_branch_request_decide(request, pk):
    """Approve, reject, or revoke a BranchAssignment."""
    assignment = get_object_or_404(BranchAssignment, pk=pk)
    decision = (request.POST.get('decision') or '').strip()
    note = (request.POST.get('note') or '').strip()[:500]

    if decision not in ('approve', 'reject', 'revoke'):
        messages.error(request, 'Invalid decision.')
        return redirect('admin_branch_requests')

    if decision == 'approve':
        if assignment.status == 'approved':
            messages.info(request, 'Already approved.')
            return redirect('admin_branch_requests')
        assignment.status = 'approved'
        admin_note = note or f'Approved by {request.user.username}'
        notif_title = '✅ Branch request approved'
        notif_body = (
            f'Your request to manage {assignment.branch.name} has been approved. '
            f'You can now process orders for that branch.'
        )
        if note:
            notif_body += f'\n\nNote from admin: {note}'

    elif decision == 'reject':
        if assignment.status == 'rejected':
            messages.info(request, 'Already rejected.')
            return redirect('admin_branch_requests')
        assignment.status = 'rejected'
        admin_note = note or f'Rejected by {request.user.username}'
        notif_title = '❌ Branch request rejected'
        notif_body = f'Your request to manage {assignment.branch.name} was not approved.'
        if note:
            notif_body += f'\n\nReason: {note}'

    else:  # revoke
        if assignment.status != 'approved':
            messages.error(request, 'Can only revoke an approved assignment.')
            return redirect('admin_branch_requests')
        assignment.status = 'revoked'
        admin_note = note or f'Revoked by {request.user.username}'
        notif_title = '⚠️ Branch access revoked'
        notif_body = f'Your access to {assignment.branch.name} has been revoked.'
        if note:
            notif_body += f'\n\nReason: {note}'

    assignment.decision_note = admin_note
    assignment.decided_at = timezone.now()
    assignment.decided_by = request.user
    assignment.save()

    # Notify the officer — in-app + WA + SMS so they don't miss the decision.
    from .notify import notify
    sms_short = f'Honey Cave: Your request to manage {assignment.branch.name} was {decision}d.'
    if note:
        sms_short += f' Note: {note[:80]}'
    notify(
        assignment.officer,
        notif_type='order_update',
        title=notif_title,
        message=notif_body,
        link='/officer/branches/',
        whatsapp_text=notif_body,
        sms_text=sms_short,
    )

    audit_log(
        request,
        f'branch_assignment_{decision}',
        f'{assignment.officer.username} → {assignment.branch.name}',
    )
    messages.success(request, f'Decision saved.')
    return redirect('admin_branch_requests')


# ─── Admin: Officer Upload-Access Requests ────────────────────────────────────

@staff_member_required
def admin_upload_requests(request):
    """
    Admin manages officer requests for product-upload access. Two paths:
      - Require payment: set a price → officer must pay before uploading.
      - Approve free:    grant upload access with no charge.
      - Reject:          deny the request (with an optional note).
    POST actions are keyed by `action` + `request_id`.
    """
    from .notify import notify

    if request.method == 'POST':
        action = request.POST.get('action', '')
        try:
            req_id = int(request.POST.get('request_id', 0))
        except (TypeError, ValueError):
            req_id = 0
        req = OfficerUploadRequest.objects.filter(pk=req_id).select_related('officer').first()
        if req is None:
            messages.error(request, 'That request no longer exists.')
            return redirect('admin_upload_requests')

        note = (request.POST.get('admin_note') or '').strip()[:300]
        officer = req.officer

        if action == 'require_payment':
            raw = (request.POST.get('amount') or '').strip()
            try:
                amount = Decimal(raw)
            except (InvalidOperation, TypeError):
                amount = Decimal('-1')
            if amount <= 0:
                messages.error(request, 'Enter a valid price greater than 0.')
                return redirect('admin_upload_requests')
            req.status = 'payment_required'
            req.amount = amount
            req.admin_note = note
            req.decided_by = request.user
            req.decided_at = timezone.now()
            req.save()
            # Make sure access is OFF until they pay.
            if hasattr(officer, 'profile'):
                officer.profile.can_upload_products = False
                officer.profile.save(update_fields=['can_upload_products'])
            try:
                notify(officer, notif_type='stock_alert',
                       title='💳 Payment required for upload access',
                       message=f'Your admin set a price of GH₵{amount:.2f} to unlock product '
                               f'uploads. Pay from your Upload Access page to continue.'
                               + (f' Note: {note}' if note else ''),
                       link='/officer/upload-access/')
            except Exception:
                pass
            messages.success(request, f'Payment of GH₵{amount:.2f} required from {officer.username}.')

        elif action == 'approve_free':
            req.status = 'approved_free'
            req.amount = None
            req.admin_note = note
            req.decided_by = request.user
            req.decided_at = timezone.now()
            req.save()
            if hasattr(officer, 'profile'):
                officer.profile.can_upload_products = True
                officer.profile.save(update_fields=['can_upload_products'])
            try:
                notify(officer, notif_type='stock_alert',
                       title='✅ Upload access granted (free)',
                       message='Your admin granted you free product-upload access. '
                               'You can start uploading now.'
                               + (f' Note: {note}' if note else ''),
                       link='/officer/product-upload/')
            except Exception:
                pass
            messages.success(request, f'{officer.username} can now upload for free.')

        elif action == 'reject':
            req.status = 'rejected'
            req.admin_note = note
            req.decided_by = request.user
            req.decided_at = timezone.now()
            req.save()
            if hasattr(officer, 'profile'):
                officer.profile.can_upload_products = False
                officer.profile.save(update_fields=['can_upload_products'])
            try:
                notify(officer, notif_type='stock_alert',
                       title='🚫 Upload access request declined',
                       message=(note or 'Your request to upload products was declined.'),
                       link='/officer/upload-access/')
            except Exception:
                pass
            messages.success(request, f'Request from {officer.username} rejected.')

        elif action == 'revoke':
            # Turn off an officer's existing access.
            if hasattr(officer, 'profile'):
                officer.profile.can_upload_products = False
                officer.profile.save(update_fields=['can_upload_products'])
            req.status = 'rejected'
            req.admin_note = note or 'Access revoked.'
            req.decided_by = request.user
            req.decided_at = timezone.now()
            req.save()
            messages.success(request, f'Upload access revoked for {officer.username}.')

        return redirect('admin_upload_requests')

    # GET — list requests, newest first, pending at the top.
    requests_qs = (OfficerUploadRequest.objects
                   .select_related('officer', 'officer__profile', 'decided_by')
                   .order_by('-created'))
    pending = [r for r in requests_qs if r.status == 'pending']
    others  = [r for r in requests_qs if r.status != 'pending']

    return render(request, 'mall/admin/upload_requests.html', {
        'pending': pending,
        'others':  others,
        'total':   requests_qs.count(),
        'pending_count': len(pending),
    })
