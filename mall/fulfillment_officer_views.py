"""
Fulfillment Officer portal — branch staff log in here to process orders for their branch.

Access control:
    - User must be authenticated (Django session)
    - User.profile.is_fulfillment_officer must be True
    - User must be assigned as `Branch.fulfillment_officer` for at least one branch
    - Fulfillment Officers can only see/act on orders for branches they manage

Views:
    fulfillment_officer_login     /officer/login/         GET/POST — uses Django auth
    fulfillment_officer_dashboard /officer/                 — list of pending orders
    fulfillment_officer_order     /officer/order/<id>/     — order detail + handoff actions
    fulfillment_officer_logout    /officer/logout/         — clear session
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import handoff as handoff_svc
from .models import (
    Order, Branch, BranchAssignment, HandoffCode, Rider, RiderDelivery,
    Product, Category, ProductImage, UserProfile, ProductUpload, ProductUploadItem,
    BranchProduct, OfficerUploadRequest,
    normalize_phone,
)
from .security import validate_uploaded_image
from decimal import Decimal
from django.utils.text import slugify
from django.db.models import Q
from django.core.files.base import ContentFile
import csv, io


def fulfillment_officer_required(view_func):
    """Decorator: only logged-in users with is_fulfillment_officer=True can pass."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        u = request.user
        if not u.is_authenticated:
            return redirect(f'/officer/login/?next={request.path}')
        prof = getattr(u, 'profile', None)
        if not prof or not prof.is_fulfillment_officer:
            return HttpResponseForbidden(
                'This area is for fulfillment officers only. '
                'If you reached this page in error, please contact the admin.'
            )
        return view_func(request, *args, **kwargs)
    return wrapper


def _branches_for(user):
    """
    Branches this fulfillment officer can currently act on.

    Reads from BranchAssignment with status='approved'. Inactive branches
    are filtered out — even if approved, an officer can't act on a
    deactivated branch.
    """
    return Branch.objects.filter(
        officer_assignments__officer=user,
        officer_assignments__status='approved',
        is_active=True,
    ).distinct()


def _branch_ids_for(user):
    """Return a list of branch IDs the officer can act on (cheap version)."""
    return list(
        BranchAssignment.objects
        .filter(officer=user, status='approved', branch__is_active=True)
        .values_list('branch_id', flat=True)
    )


# ─── Auth ─────────────────────────────────────────────────────────────────────

@require_http_methods(['GET', 'POST'])
def fulfillment_officer_login(request):
    """
    Dedicated login page for fulfillment officers. Same Django auth backend as the
    main /login/ but redirects to /officer/ on success and rejects users
    who aren't flagged is_fulfillment_officer.
    """
    next_url = request.GET.get('next') or request.POST.get('next') or '/officer/'
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, 'Invalid username or password.')
        else:
            prof = getattr(user, 'profile', None)
            if not prof or not prof.is_fulfillment_officer:
                messages.error(request, 'This account is not authorised for the fulfillment officer portal.')
            else:
                auth_login(request, user)
                return redirect(next_url if next_url.startswith('/officer') else '/officer/')
    return render(request, 'mall/fulfillment_officer/login.html', {'next': next_url})


def fulfillment_officer_logout(request):
    auth_logout(request)
    return redirect('/officer/login/')


# ─── Dashboard ────────────────────────────────────────────────────────────────

@fulfillment_officer_required
def fulfillment_officer_dashboard(request):
    """
    Lists orders relevant to this fulfillment officer, grouped by stage:
      - Awaiting confirmation  (admin issued code, keeper hasn't entered it yet)
      - With rider             (keeper handed off; rider hasn't confirmed)
      - With pickup customer   (keeper has package; customer hasn't collected)
      - Recently completed     (last 20 verified handoffs from this branch)
    """
    branches = _branches_for(request.user)
    orders = (Order.objects.filter(branch__in=branches)
              .exclude(status__in=['delivered', 'cancelled'])
              .select_related('branch', 'rider_delivery')
              .prefetch_related('handoff_codes')
              .order_by('-created'))

    awaiting_keeper   = []
    with_rider        = []
    with_pickup_cust  = []
    other             = []

    for order in orders:
        codes_by_stage = _codes_by_stage(order)
        admin_code  = codes_by_stage.get('admin_to_officer')
        rider_code  = codes_by_stage.get('officer_to_rider')
        pickup_code = codes_by_stage.get('officer_to_customer')
        rd_code     = codes_by_stage.get('rider_to_customer')

        if admin_code and not admin_code.is_verified and not admin_code.locked:
            awaiting_keeper.append((order, admin_code))
        elif order.fulfillment_type == 'pickup' and pickup_code and not pickup_code.is_verified:
            with_pickup_cust.append((order, pickup_code))
        elif order.fulfillment_type == 'delivery' and rider_code and not rider_code.is_verified:
            with_rider.append((order, rider_code))
        elif order.fulfillment_type == 'delivery' and rd_code and not rd_code.is_verified:
            with_rider.append((order, rd_code))
        else:
            other.append(order)

    return render(request, 'mall/fulfillment_officer/dashboard.html', {
        'branches':         branches,
        'awaiting_keeper':  awaiting_keeper,
        'with_rider':       with_rider,
        'with_pickup_cust': with_pickup_cust,
        'other':            other,
    })


# ─── Order detail + actions ───────────────────────────────────────────────────

@fulfillment_officer_required
def fulfillment_officer_order(request, pk):
    """
    Order detail page for the fulfillment officer. Shows the right action depending
    on which stage of the handoff the order is in:

      - admin_to_keeper not verified yet  → form to enter Code 1
      - keeper_to_rider issued, awaiting  → display Code 2 for the rider
      - keeper_to_customer issued (pickup) → display Code 2 for the customer
      - everything done                   → read-only summary
    """
    branches = _branches_for(request.user)
    order = get_object_or_404(Order, pk=pk, branch__in=branches)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'verify_admin_code':
            entered = request.POST.get('code', '').strip()
            status, _, remaining = handoff_svc.verify_code(
                order, 'admin_to_officer', entered, used_by_user=request.user
            )
            if status == 'ok':
                messages.success(request, '✓ Order confirmed received. Next code has been auto-issued.')
            elif status == 'wrong':
                messages.error(request, f'Wrong code. {remaining} attempt(s) left.')
            elif status == 'locked':
                messages.error(request, '🔒 Locked after 3 wrong attempts. Admin has been notified.')
            elif status == 'expired':
                messages.error(request, 'Code expired. Please ask admin to issue a new one.')
            elif status == 'not_found':
                messages.error(request, 'No code has been issued for this order yet. Contact admin.')
            return redirect('fulfillment_officer_order', pk=order.pk)

        if action == 'assign_rider':
            # Fulfillment Officer assigns a rider once they have the package and are
            # ready to send out for delivery.
            #
            # NEW (hybrid roster) flow:
            #   mode='roster'  → request.POST['rider_id'] points to a Rider record.
            #                     Snapshot rider_name/rider_phone from that record.
            #   mode='adhoc'   → request.POST['rider_name'] + ['rider_phone'] are
            #                     typed-in values. We:
            #                       (a) check whether a Rider with that phone
            #                           already exists — if yes, use it (and warn
            #                           if it's deactivated/different name);
            #                       (b) otherwise auto-create a draft Rider with
            #                           is_verified=False so admin can review.
            #                     The RiderDelivery row is marked is_adhoc=True.
            #
            # Either path: chain advances to officer_to_rider.

            mode = request.POST.get('rider_mode', 'roster')

            # Stage check — chain integrity comes first
            if order.fulfillment_type != 'delivery':
                messages.error(request, 'Riders can only be assigned to delivery orders.')
                return redirect('fulfillment_officer_order', pk=order.pk)

            keeper_confirmed = order.handoff_codes.filter(
                stage='admin_to_officer', used_at__isnull=False,
            ).exists()
            if not keeper_confirmed:
                messages.error(
                    request,
                    'You must confirm receipt of the order from the admin '
                    'before assigning a rider.',
                )
                return redirect('fulfillment_officer_order', pk=order.pk)

            rider_record = None
            rider_name = ''
            rider_phone = ''
            is_adhoc = False

            if mode == 'roster':
                rider_id = request.POST.get('rider_id', '').strip()
                if not rider_id:
                    messages.error(request, 'Please pick a rider from the roster.')
                    return redirect('fulfillment_officer_order', pk=order.pk)
                # Constrain pick to riders who serve the order's branch and are active
                rider_record = Rider.objects.filter(
                    pk=rider_id,
                    is_active=True,
                    branches=order.branch,
                ).first()
                if not rider_record:
                    messages.error(
                        request,
                        'That rider isn\'t available for this branch. '
                        'Pick another, or use the ad-hoc option.',
                    )
                    return redirect('fulfillment_officer_order', pk=order.pk)
                rider_name = rider_record.name
                rider_phone = rider_record.phone
            else:
                # Ad-hoc path
                rider_name = (request.POST.get('rider_name') or '').strip()
                rider_phone = (request.POST.get('rider_phone') or '').strip()
                if not rider_name or not rider_phone:
                    messages.error(request, 'Rider name and phone number are both required.')
                    return redirect('fulfillment_officer_order', pk=order.pk)
                if len(rider_name) > 100 or len(rider_phone) > 30:
                    messages.error(request, 'Name or phone number too long.')
                    return redirect('fulfillment_officer_order', pk=order.pk)
                # Phone match against the roster — re-use if exists
                existing = Rider.find_by_phone(rider_phone, active_only=False)
                if existing:
                    if not existing.is_active:
                        messages.error(
                            request,
                            f'A rider with phone {rider_phone} is deactivated in the roster. '
                            f'Ask admin to reactivate them, or use a different phone.',
                        )
                        return redirect('fulfillment_officer_order', pk=order.pk)
                    rider_record = existing
                    # Use the existing record's name unless officer explicitly typed
                    # a different one — then update it (admin will review).
                    if rider_name and rider_name != existing.name:
                        existing.name = rider_name
                        existing.save(update_fields=['name'])
                    rider_name = existing.name
                    is_adhoc = False  # actually a known rider, just dispatched ad-hoc-style
                else:
                    # New rider — auto-draft as unverified
                    rider_record = Rider.objects.create(
                        name=rider_name,
                        phone=rider_phone,
                        is_active=True,
                        is_verified=False,
                        created_by=request.user,
                        notes=f'Auto-drafted from ad-hoc dispatch on order {order.order_number}.',
                    )
                    rider_record.branches.add(order.branch)
                    is_adhoc = True
                    # Notify admins for review
                    from .notify import notify_admins
                    notify_admins(
                        notif_type='order_update',
                        title='⚠️ New rider auto-drafted — review needed',
                        message=(
                            f'Officer {request.user.username} dispatched ad-hoc to '
                            f'a new rider "{rider_name}" ({rider_phone}) on order '
                            f'{order.order_number}. Review and verify or remove.'
                        ),
                        link='/panel/riders/?status=unverified',
                    )

            # Create or update the delivery row
            try:
                existing_delivery = order.rider_delivery
            except Exception:
                existing_delivery = None
            if existing_delivery:
                existing_delivery.rider       = rider_record
                existing_delivery.rider_name  = rider_name
                existing_delivery.rider_phone = rider_phone
                existing_delivery.is_adhoc   = is_adhoc
                existing_delivery.save()
                rider_obj = existing_delivery
                is_new = False
                messages.success(request, f'Rider details updated: {rider_name}.')
            else:
                rider_obj = RiderDelivery.objects.create(
                    order=order,
                    rider=rider_record,
                    is_adhoc=is_adhoc,
                    rider_name=rider_name,
                    rider_phone=rider_phone,
                )
                order.status = 'dispatched'
                order.save(update_fields=['status'])
                is_new = True
                messages.success(request, f'Rider {rider_name} assigned. Order marked Dispatched.')

            # Auto-issue the officer_to_rider code (rider's pickup code)
            handoff_svc.issue_code(
                order, 'officer_to_rider',
                issued_to_label=f'Rider: {rider_name}',
            )

            # Send magic-link portal URL to the rider via WhatsApp + SMS.
            try:
                from . import whatsapp as _wa
                _wa.notify_rider_assigned(rider_obj, request=request)
            except Exception:
                pass

            # Notify admin (in-app) about the assignment.
            from .notify import notify_admins
            action_text = 'assigned' if is_new else 'updated'
            notify_admins(
                notif_type='rider_dispatched',
                title='🛵 Rider assigned by fulfillment officer',
                message=(
                    f'Fulfillment Officer {request.user.username} {action_text} rider '
                    f'{rider_name} ({rider_phone}) on order {order.order_number}.'
                ),
                link=f'/panel/orders/{order.id}/',
            )

            # Notify customer (in-app + WA + SMS) — only on first assignment
            # (avoid spamming on every rider update).
            if is_new:
                from .views import _notify_rider_dispatched
                _notify_rider_dispatched(order, rider_obj)

            return redirect('fulfillment_officer_order', pk=order.pk)

    codes_by_stage = _codes_by_stage(order)
    # OneToOneField reverse lookup raises if no related object exists,
    # so wrap in try/except. The attribute hasattr() check works too.
    try:
        rider_delivery = order.rider_delivery
    except Exception:
        rider_delivery = None

    # Roster of riders the officer can pick from for THIS order's branch.
    # Verified riders shown first, then unverified — both alphabetical.
    # Inactive riders are always excluded.
    roster = (Rider.objects
              .filter(is_active=True, branches=order.branch)
              .order_by('-is_verified', 'name'))

    return render(request, 'mall/fulfillment_officer/order_detail.html', {
        'order':           order,
        'admin_code':      codes_by_stage.get('admin_to_officer'),
        'rider_code':      codes_by_stage.get('officer_to_rider'),
        'pickup_code':     codes_by_stage.get('officer_to_customer'),
        'rider_delivery':  rider_delivery,
        # Render QR for whichever code is currently issued by the keeper
        'show_qr_for':     _active_outgoing_code(order, codes_by_stage),
        'rider_roster':    roster,
    })


# ─── Helpers ──────────────────────────────────────────────────────────────────

def fulfillment_officer_history(request):
    """
    Past-orders history for the logged-in fulfillment officer. Shows all orders at
    their branch from the last 90 days, with optional search by order number
    or customer name and a status filter.

    Closed orders (delivered, cancelled) are the focus here — but we include
    every order in the time window so a fulfillment officer can find anything they
    need. Active orders also appear in the regular dashboard, so this is
    the single source of truth for past work.
    """
    if not request.user.is_authenticated:
        return redirect('fulfillment_officer_login')
    profile = getattr(request.user, 'profile', None)
    if not profile or not profile.is_fulfillment_officer:
        messages.error(request, 'Fulfillment Officer access required.')
        return redirect('fulfillment_officer_login')

    # Find the branch this fulfillment officer manages
    branch = Branch.objects.filter(fulfillment_officer=request.user).first()
    if branch is None:
        messages.error(request, 'You are not assigned to a branch yet. Contact admin.')
        return redirect('fulfillment_officer_login')

    # 90-day window — anything older is hidden to keep the page fast
    from django.utils import timezone as _tz
    from datetime import timedelta
    cutoff = _tz.now() - timedelta(days=90)

    orders = (Order.objects
              .filter(branch=branch, created__gte=cutoff)
              .select_related('user')
              .prefetch_related('items')
              .order_by('-created'))

    # Optional filters
    q = (request.GET.get('q') or '').strip()
    if q:
        orders = orders.filter(
            Q(order_number__icontains=q) |
            Q(full_name__icontains=q) |
            Q(phone__icontains=q)
        )

    status_filter = (request.GET.get('status') or '').strip()
    if status_filter and status_filter != 'all':
        orders = orders.filter(status=status_filter)

    fulfillment_filter = (request.GET.get('fulfillment') or '').strip()
    if fulfillment_filter and fulfillment_filter in ('delivery', 'pickup'):
        orders = orders.filter(fulfillment_type=fulfillment_filter)

    # Pagination — 25 per page is the sweet spot for table views
    paginator = Paginator(orders, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'mall/fulfillment_officer/history.html', {
        'branch':             branch,
        'page_obj':           page_obj,
        'q':                  q,
        'status_filter':      status_filter or 'all',
        'fulfillment_filter': fulfillment_filter or 'all',
        'cutoff_days':        90,
    })


def _codes_by_stage(order):
    """
    Return {stage: latest HandoffCode} dict. Latest = newest by created_at.
    Used for templates to look up "the current admin_to_keeper code" etc.
    """
    out = {}
    for h in order.handoff_codes.all():
        existing = out.get(h.stage)
        if existing is None or h.created_at > existing.created_at:
            out[h.stage] = h
    return out


def _active_outgoing_code(order, codes_by_stage):
    """
    Return the HandoffCode the keeper should currently be SHOWING to the next
    party (rider or pickup customer) — i.e. the one they need to display QR
    + numbers for. None if there isn't one active right now.
    """
    if order.fulfillment_type == 'pickup':
        c = codes_by_stage.get('officer_to_customer')
    else:
        c = codes_by_stage.get('officer_to_rider')
    if c and c.is_active:
        return c
    return None


# ─── My Branches (officer-side branch management & requests) ─────────────────

@fulfillment_officer_required
def fulfillment_officer_branches(request):
    """
    Officer's "My Branches" page.

    Shows three sections:
      - Approved assignments (branches the officer can act on right now)
      - Pending requests (the officer is waiting on admin)
      - Past decisions (rejected / revoked — for transparency)

    Plus: a list of branches the officer DOESN'T have any assignment for,
    with a "Request to manage" button per branch. Submitting creates a
    BranchAssignment(role='secondary', status='pending').
    """
    user = request.user

    my_assignments = (BranchAssignment.objects
                      .filter(officer=user)
                      .select_related('branch', 'decided_by')
                      .order_by('-status', 'role', '-requested_at'))

    approved = [a for a in my_assignments if a.status == 'approved']
    pending  = [a for a in my_assignments if a.status == 'pending']
    history  = [a for a in my_assignments if a.status in ('rejected', 'revoked')]

    # Branches the officer doesn't have any record for yet (eligible to request)
    has_assignment_for = set(a.branch_id for a in my_assignments)
    requestable = (Branch.objects
                   .filter(is_active=True)
                   .exclude(id__in=has_assignment_for)
                   .order_by('region', 'name'))

    return render(request, 'mall/fulfillment_officer/branches.html', {
        'approved':    approved,
        'pending':     pending,
        'history':     history,
        'requestable': requestable,
    })


@fulfillment_officer_required
@require_http_methods(['POST'])
def fulfillment_officer_request_branch(request):
    """
    Officer requests to manage an additional branch.

    Creates a BranchAssignment(role='secondary', status='pending').
    Notifies all admins via in-app Notification so they can review.
    """
    from .models import Notification
    from django.contrib.auth.models import User as _User

    branch_id = (request.POST.get('branch_id') or '').strip()
    note      = (request.POST.get('note') or '').strip()[:500]

    if not branch_id:
        messages.error(request, 'Please pick a branch to request.')
        return redirect('fulfillment_officer_branches')

    branch = Branch.objects.filter(id=branch_id, is_active=True).first()
    if not branch:
        messages.error(request, 'That branch is not available.')
        return redirect('fulfillment_officer_branches')

    # Reject duplicates — there shouldn't already be a pending/approved row.
    existing = BranchAssignment.objects.filter(officer=request.user, branch=branch).first()
    if existing:
        if existing.status == 'pending':
            messages.info(request, f'You already have a pending request for {branch.name}.')
        elif existing.status == 'approved':
            messages.info(request, f'You\'re already assigned to {branch.name}.')
        else:
            # Re-open a rejected/revoked one by flipping back to pending.
            existing.status = 'pending'
            existing.requested_at = timezone.now()
            existing.requested_by = request.user
            existing.decided_at = None
            existing.decided_by = None
            existing.decision_note = (
                (existing.decision_note + '\n\n' if existing.decision_note else '')
                + f'[Re-requested {timezone.now():%Y-%m-%d %H:%M}'
                + (f': {note}' if note else '')
                + ']'
            )
            existing.save()
            messages.success(request, f'Re-submitted request for {branch.name}.')
            _notify_admins_branch_request(existing, note)
        return redirect('fulfillment_officer_branches')

    # New request
    assignment = BranchAssignment.objects.create(
        officer=request.user,
        branch=branch,
        role='secondary',
        status='pending',
        requested_by=request.user,
        decision_note=note,
    )
    messages.success(
        request,
        f'Request sent to manage {branch.name}. Admin will review it.',
    )
    _notify_admins_branch_request(assignment, note)
    return redirect('fulfillment_officer_branches')


def _notify_admins_branch_request(assignment, note=''):
    """In-app notification to all admins about a new branch assignment request."""
    from .notify import notify_admins
    body = (
        f'Officer {assignment.officer.username} requested to manage '
        f'{assignment.branch.name}.'
    )
    if note:
        body += f'\n\nNote: {note}'
    notify_admins(
        notif_type='order_update',
        title=f'📋 New branch request from {assignment.officer.username}',
        message=body,
        link='/panel/branch-requests/',
    )


# ─── Officer-managed rider roster ────────────────────────────────────────

@fulfillment_officer_required
def fulfillment_officer_riders(request):
    """
    Officer's view of the rider roster scoped to THEIR branches.

    Shows riders associated with any branch the officer is approved for,
    with the same verified/unverified/inactive split admins see. Officers
    can add a new rider here directly (gets is_verified=False, admin
    reviews) — same flow as the ad-hoc auto-draft path, but proactive.
    """
    branches_qs = _branches_for(request.user)
    branch_ids  = list(branches_qs.values_list('id', flat=True))

    riders = (Rider.objects
              .filter(branches__id__in=branch_ids)
              .distinct()
              .prefetch_related('branches')
              .order_by('-is_verified', '-is_active', 'name'))

    return render(request, 'mall/fulfillment_officer/riders.html', {
        'riders':   riders,
        'branches': branches_qs,
    })


@fulfillment_officer_required
@require_http_methods(['GET', 'POST'])
def fulfillment_officer_rider_add(request):
    """
    Officer adds a new rider to one of THEIR branches.

    The rider is created with is_verified=False so admin reviews them
    before they're shown as ✓ in dropdowns. Officers can still dispatch
    to unverified riders (same as ad-hoc) — verification is an admin
    quality-gate, not a hard block.

    Phone uniqueness is enforced at the DB level. If the typed phone
    matches an existing rider, we redirect to the branch picker so the
    officer can ATTACH the existing rider to one of their branches
    instead of creating a duplicate.
    """
    branches_qs = _branches_for(request.user)
    if not branches_qs.exists():
        messages.error(
            request,
            'You can only add riders for branches you manage. '
            'No approved branches found on your account.',
        )
        return redirect('fulfillment_officer_riders')

    if request.method == 'POST':
        name          = (request.POST.get('name') or '').strip()
        phone         = (request.POST.get('phone') or '').strip()
        vehicle_type  = (request.POST.get('vehicle_type') or 'motorcycle').strip()
        license_no    = (request.POST.get('license_number') or '').strip()
        notes         = (request.POST.get('notes') or '').strip()
        branch_ids    = request.POST.getlist('branch_ids')

        if not name or not phone:
            messages.error(request, 'Name and phone are required.')
            return render(request, 'mall/fulfillment_officer/rider_form.html', {
                'branches': branches_qs,
                'vehicle_choices': Rider.VEHICLE_CHOICES,
                'form_data': request.POST,
            })

        # Validate that every chosen branch is actually one this officer
        # manages. Without this, a hand-crafted form post could add a
        # rider to ANY branch — soft-trust is not enough on a roster.
        allowed = set(branches_qs.values_list('id', flat=True))
        chosen  = {int(b) for b in branch_ids if str(b).isdigit()}
        chosen &= allowed
        if not chosen:
            messages.error(request, 'Pick at least one of YOUR branches for this rider.')
            return render(request, 'mall/fulfillment_officer/rider_form.html', {
                'branches': branches_qs,
                'vehicle_choices': Rider.VEHICLE_CHOICES,
                'form_data': request.POST,
            })

        # Phone uniqueness
        normalized_phone = normalize_phone(phone)
        if not normalized_phone:
            messages.error(request, 'Please enter a valid phone number.')
            return render(request, 'mall/fulfillment_officer/rider_form.html', {
                'branches': branches_qs,
                'vehicle_choices': Rider.VEHICLE_CHOICES,
                'form_data': request.POST,
            })

        existing = Rider.objects.filter(phone=normalized_phone).first()
        if existing:
            # Don't create a duplicate. Add officer's branches to the
            # existing rider (officer is implicitly trusted for THEIR
            # branches). Tell them what happened.
            added = []
            for b_id in chosen:
                if not existing.branches.filter(id=b_id).exists():
                    existing.branches.add(b_id)
                    added.append(b_id)
            if existing.is_active:
                if added:
                    branch_names = ', '.join(
                        Branch.objects.filter(id__in=added).values_list('name', flat=True)
                    )
                    messages.success(
                        request,
                        f'A rider with phone {phone} already exists ({existing.name}). '
                        f'Added to {branch_names}.',
                    )
                else:
                    messages.info(
                        request,
                        f'Rider {existing.name} ({phone}) is already in your roster.',
                    )
                return redirect('fulfillment_officer_riders')
            else:
                messages.error(
                    request,
                    f'A rider with phone {phone} exists but is deactivated. '
                    f'Ask admin to reactivate them.',
                )
                return redirect('fulfillment_officer_riders')

        # Create new rider — unverified until admin reviews
        rider = Rider.objects.create(
            name=name,
            phone=phone,
            vehicle_type=vehicle_type,
            license_number=license_no,
            notes=notes,
            is_active=True,
            is_verified=False,    # officer-added → admin reviews
            created_by=request.user,
        )
        rider.branches.add(*chosen)

        # Notify admins so they can verify
        from .notify import notify_admins
        branch_names = ', '.join(
            Branch.objects.filter(id__in=chosen).values_list('name', flat=True)
        )
        notify_admins(
            notif_type='order_update',
            title='🆕 New rider added by officer (review needed)',
            message=(
                f'Officer {request.user.username} added a new rider: '
                f'{name} ({phone}) for {branch_names}. '
                f'Review and verify in Panel → Riders.'
            ),
            link='/panel/riders/?status=unverified',
        )

        messages.success(
            request,
            f'Rider {name} added. Status: unverified — admin will review. '
            f'You can dispatch to them right away.',
        )
        return redirect('fulfillment_officer_riders')

    return render(request, 'mall/fulfillment_officer/rider_form.html', {
        'branches': branches_qs,
        'vehicle_choices': Rider.VEHICLE_CHOICES,
        'form_data': {},
    })


# ─── Officer Product Upload ────────────────────────────────────────────────────

def _officer_can_upload(user):
    """Return True if this officer has been granted upload permission by admin."""
    try:
        return user.profile.can_upload_products
    except Exception:
        return False


@fulfillment_officer_required
def officer_product_upload(request):
    """Officer: add a single product with image upload (if permitted)."""
    if not _officer_can_upload(request.user):
        return redirect('officer_upload_access')

    categories = Category.objects.all().order_by('name')

    if request.method == 'POST':
        try:
            name  = request.POST.get('name', '').strip()[:200]
            if not name:
                raise ValueError('Product name is required.')
            desc  = request.POST.get('description', '').strip()[:5000]
            price_raw = request.POST.get('price', '').strip()
            stock_raw = request.POST.get('stock', '0').strip()
            cat_id    = request.POST.get('category', '').strip()

            try:
                price = Decimal(price_raw)
                if price <= 0:
                    raise ValueError()
            except Exception:
                raise ValueError('Price must be a positive number.')

            try:
                stock = int(stock_raw)
                if stock < 0:
                    raise ValueError()
            except Exception:
                raise ValueError('Stock must be a non-negative whole number.')

            if not cat_id:
                raise ValueError('Please select a category.')

            category = Category.objects.filter(pk=cat_id).first()
            if not category:
                raise ValueError('Selected category does not exist.')

            slug = slugify(name)
            base_slug, n = slug, 1
            while Product.objects.filter(slug=slug).exists():
                slug = f'{base_slug}-{n}'; n += 1

            p = Product(
                name=name, slug=slug, description=desc,
                price=price, stock=stock, category=category,
                available='available' in request.POST,
                created_by=request.user,   # so it shows under "My Products"
            )

            img = request.FILES.get('image')
            if img:
                err = validate_uploaded_image(img)
                if err:
                    raise ValueError(f'Image: {err}')
                p.image = img

            p.save()

            # Stock the product at every branch this officer manages.
            # Strict-stock policy: a BranchProduct row is what makes the
            # product actually sellable at a branch — without it the product
            # would exist in the catalogue but never appear at the officer's
            # branch. Price defaults to the catalogue price; admin/officer can
            # adjust per-branch later.
            officer_branches = _branches_for(request.user)
            for b in officer_branches:
                BranchProduct.objects.get_or_create(
                    product=p, branch=b,
                    defaults={'price': price, 'stock': stock, 'is_available': True},
                )

            # Additional gallery images (up to 5 extra)
            for slot in range(2, 7):
                extra = request.FILES.get(f'image_{slot}')
                if extra:
                    err = validate_uploaded_image(extra)
                    if err:
                        messages.warning(request, f'Image slot {slot} skipped: {err}')
                        continue
                    ProductImage.objects.create(product=p, image=extra, sort_order=slot)

            messages.success(request, f'Product "{p.name}" added successfully and stocked at your branch.')
            return redirect('officer_my_products')

        except ValueError as e:
            messages.error(request, str(e))

    # Show the products the admin assigned to this officer (responsibility list)
    # right on the upload page, so they can track what they're meant to manage.
    try:
        assigned_products = (request.user.profile.assigned_products
                             .select_related('category')
                             .prefetch_related('branch_pricing__branch')
                             .order_by('name'))
    except Exception:
        assigned_products = []

    return render(request, 'mall/fulfillment_officer/product_upload.html', {
        'categories': categories,
        'assigned_products': assigned_products,
    })


@fulfillment_officer_required
def officer_csv_upload(request):
    """Officer: bulk import products via CSV (if permitted)."""
    if not _officer_can_upload(request.user):
        messages.error(request, 'You do not have permission to upload products.')
        return redirect('fulfillment_officer_dashboard')

    results = None

    if request.method == 'POST':
        f = request.FILES.get('csv_file')
        if not f:
            messages.error(request, 'Please select a CSV file.')
            return render(request, 'mall/fulfillment_officer/csv_upload.html', {'results': results})

        try:
            data = f.read()
            raw = data.decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(raw))
            rows = list(reader)
            if not rows:
                raise ValueError('The CSV file appears to be empty.')
        except Exception as e:
            messages.error(request, f'Could not read CSV: {e}')
            return render(request, 'mall/fulfillment_officer/csv_upload.html', {'results': results})

        upload = ProductUpload.objects.create(
            officer=request.user,
            total_items=len(rows),
            approved_items=0,
            rejected_items=0,
        )
        upload.csv_file.save(f.name, ContentFile(data), save=False)
        upload.save()

        valid_count = 0
        error_rows = []
        cat_cache = {}

        for i, row in enumerate(rows, start=2):
            name = row.get('name', '').strip()
            cat_name = row.get('category', '').strip()
            price_v = row.get('price', '').strip()
            description = row.get('description', '').strip()
            sku = row.get('sku', '').strip()
            category_name = cat_name or 'Uncategorized'
            image_filename = row.get('image_filename', '').strip()

            try:
                if not name:
                    raise ValueError('name is required')
                if not cat_name:
                    raise ValueError('category is required')
                if not price_v:
                    raise ValueError('price is required')
                price = Decimal(price_v)
                if price <= 0:
                    raise ValueError('price must be a positive number')

                if cat_name.lower() not in cat_cache:
                    cat_obj, _ = Category.objects.get_or_create(
                        name__iexact=cat_name,
                        defaults={
                            'name': cat_name,
                            'slug': slugify(cat_name) or f'cat-{cat_name[:20]}',
                        },
                    )
                    cat_cache[cat_name.lower()] = cat_obj

                ProductUploadItem.objects.create(
                    upload=upload,
                    product_name=name,
                    price=price,
                    description=description,
                    sku=sku,
                    category_name=category_name,
                    image_filename=image_filename,
                    status='pending',
                )
                valid_count += 1
            except Exception as e:
                error_rows.append({'row': i, 'name': row.get('name', '?'), 'reason': str(e)})

        upload.rejected_items = len(error_rows)
        upload.approved_items = 0
        upload.status = 'pending' if valid_count else 'rejected'
        upload.save(update_fields=['rejected_items', 'approved_items', 'status'])

        results = {
            'upload_id': upload.id,
            'total_rows': len(rows),
            'valid_items': valid_count,
            'errors': len(error_rows),
            'error_rows': error_rows,
        }

        if valid_count:
            messages.success(request, f'CSV received: {valid_count} product item(s) queued for admin review.')
        if error_rows:
            messages.warning(request, f'{len(error_rows)} row(s) had errors and were skipped.')

    return render(request, 'mall/fulfillment_officer/csv_upload.html', {'results': results})


# ─── My Products — everything this officer has uploaded ──────────────────────

@fulfillment_officer_required
def officer_my_products(request):
    """
    Officer: list the products this officer uploaded (single uploads) plus
    every product stocked at the officer's branches, so they can see exactly
    what customers see on their side.

    Filters:
        ?q=<search>        — name contains
        ?scope=mine        — only products I uploaded (default)
        ?scope=branch      — everything stocked at my branches
    """
    if not _officer_can_upload(request.user):
        messages.error(request, 'You do not have permission to view product uploads.')
        return redirect('fulfillment_officer_dashboard')

    branches   = _branches_for(request.user)
    branch_ids = [b.id for b in branches]
    scope      = request.GET.get('scope', 'mine')
    q          = (request.GET.get('q') or '').strip()[:100]

    if scope == 'branch':
        products = (Product.objects
                    .filter(branch_pricing__branch_id__in=branch_ids)
                    .distinct())
    else:
        scope    = 'mine'
        products = Product.objects.filter(created_by=request.user)

    if q:
        products = products.filter(Q(name__icontains=q) | Q(description__icontains=q))

    products = (products
                .select_related('category')
                .prefetch_related('branch_pricing__branch')
                .order_by('-created'))

    paginator = Paginator(products, 24)
    page = paginator.get_page(request.GET.get('page'))

    # Annotate each product with its stock rows at THIS officer's branches
    rows = []
    for p in page.object_list:
        my_branch_rows = [bp for bp in p.branch_pricing.all() if bp.branch_id in branch_ids]
        rows.append({'product': p, 'branch_rows': my_branch_rows})

    return render(request, 'mall/fulfillment_officer/my_products.html', {
        'rows':      rows,
        'page':      page,
        'paginator': paginator,
        'scope':     scope,
        'q':         q,
        'branches':  branches,
        'total':     paginator.count,
    })


# ─── Officer Upload Access: request → (pay | free) → granted ──────────────────

@fulfillment_officer_required
def officer_upload_access(request):
    """
    Landing page for upload access. Shows the officer the current state of
    their request and the right call-to-action:
      - no request / rejected  → "Request upload access" button
      - pending                → "awaiting admin review"
      - payment_required       → "Pay GH₵X" button (Paystack)
      - paid / approved_free   → already granted, link to upload
    """
    from django.conf import settings as dj_settings

    # Already has access — straight to upload.
    if _officer_can_upload(request.user):
        return redirect('officer_product_upload')

    req = (OfficerUploadRequest.objects
           .filter(officer=request.user)
           .exclude(status='rejected')
           .order_by('-created')
           .first())
    # If the only requests are rejected, surface the latest one so the officer
    # sees the admin's note (but can request again).
    if req is None:
        req = (OfficerUploadRequest.objects
               .filter(officer=request.user).order_by('-created').first())

    paystack_public_key = dj_settings.PAYSTACK_PUBLIC_KEY
    paystack_configured = bool(
        paystack_public_key
        and paystack_public_key.startswith(('pk_live_', 'pk_test_'))
    )

    amount_pesewas = 0
    if req and req.status == 'payment_required' and req.amount:
        amount_pesewas = int(req.amount * 100)

    return render(request, 'mall/fulfillment_officer/upload_access.html', {
        'req': req,
        'paystack_public_key': paystack_public_key,
        'paystack_configured': paystack_configured,
        'officer_email': request.user.email or '',
        'amount_pesewas': amount_pesewas,
    })


@fulfillment_officer_required
@require_POST
def officer_request_upload_access(request):
    """Officer submits (or re-submits) a request to upload products."""
    if _officer_can_upload(request.user):
        return redirect('officer_product_upload')

    # If there's already an open (non-rejected) request, don't duplicate.
    existing = (OfficerUploadRequest.objects
                .filter(officer=request.user)
                .exclude(status__in=['rejected'])
                .exclude(status__in=['paid', 'approved_free'])
                .first())
    if existing:
        messages.info(request, 'You already have a request in progress.')
        return redirect('officer_upload_access')

    note = (request.POST.get('note') or '').strip()[:300]
    OfficerUploadRequest.objects.create(
        officer=request.user, status='pending', admin_note='',
    )
    # Notify admins.
    try:
        from .models import User as _U
        from .notify import notify as _notify
        for staff in _U.objects.filter(is_staff=True, is_active=True):
            _notify(
                user=staff,
                notif_type='stock_alert',
                title='🧾 Officer requested upload access',
                message=f'{request.user.get_full_name() or request.user.username} '
                        f'is requesting permission to upload products. '
                        f'Set a price or approve for free.'
                        + (f' Note: {note}' if note else ''),
                link='/panel/upload-requests/',
            )
    except Exception:
        pass

    messages.success(request, 'Request sent. Your admin will set a price or grant free access.')
    return redirect('officer_upload_access')


@fulfillment_officer_required
@require_POST
def officer_upload_pay_verify(request):
    """
    AJAX: verify the officer's Paystack payment for upload access.
    On success (amount paid >= admin-set price) → mark paid + grant upload.
    """
    from .payments import dispatch as _dispatch
    import json as _json

    try:
        body = _json.loads(request.body)
        reference = (body.get('reference') or '').strip()
    except Exception:
        return JsonResponse({'verified': False, 'error': 'Invalid request.'}, status=400)

    if not reference:
        return JsonResponse({'verified': False, 'error': 'No reference supplied.'}, status=400)

    req = (OfficerUploadRequest.objects
           .filter(officer=request.user, status='payment_required')
           .order_by('-created').first())
    if req is None or req.amount is None:
        return JsonResponse({'verified': False, 'error': 'No payment is currently required for your account.'}, status=400)

    adapter, _ps = _dispatch.gateway_by_provider('paystack')
    if adapter is None:
        return JsonResponse({'verified': False, 'error': 'Paystack is not configured.'}, status=500)

    result = adapter.verify_payment(reference)
    if not result.success or not result.is_paid:
        return JsonResponse(
            {'verified': False, 'error': result.error_message or 'Payment not successful.'},
            status=400,
        )

    # Anti-tamper: amount actually paid must cover the admin-set price.
    required_pesewas = int(req.amount * 100)
    if result.amount_pesewas < required_pesewas:
        return JsonResponse({
            'verified': False,
            'error': f'Amount paid (GH₵{result.amount_pesewas/100:.2f}) is less than the '
                     f'required GH₵{req.amount:.2f}.',
        }, status=400)

    # Grant access.
    req.status = 'paid'
    req.payment_reference = reference
    req.amount_paid = result.amount_pesewas / 100
    req.decided_at = timezone.now()
    req.save()

    prof = request.user.profile
    prof.can_upload_products = True
    prof.save(update_fields=['can_upload_products'])

    # Notify admins of the successful payment.
    try:
        from .models import User as _U
        from .notify import notify as _notify
        for staff in _U.objects.filter(is_staff=True, is_active=True):
            _notify(
                user=staff,
                notif_type='stock_alert',
                title='✅ Officer paid for upload access',
                message=f'{request.user.get_full_name() or request.user.username} paid '
                        f'GH₵{req.amount_paid:.2f} (ref {reference}). Upload access granted.',
                link='/panel/upload-requests/',
            )
    except Exception:
        pass

    return JsonResponse({'verified': True, 'redirect': '/officer/product-upload/'})
