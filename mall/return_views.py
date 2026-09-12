"""
Customer return-request flow, plus admin decision/refund handling.
Kept separate from views.py / admin_views.py so it doesn't require
touching their existing imports.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import OrderItem, ReturnRequest, HandoffCode
from .wallet import credit_rider_for_rejection
from .forms import ReturnRequestForm
from .admin_views import staff_member_required, audit_log
from .notify import notify


@login_required
def request_return(request, item_id):
    """Customer requests a return for a specific delivered OrderItem."""
    item = get_object_or_404(OrderItem, pk=item_id, order__user=request.user)
    order = item.order

    if order.status not in ('delivered', 'confirmed'):
        messages.error(request, 'Returns can only be requested for delivered orders.')
        return redirect('my_orders')

    handoff = HandoffCode.objects.filter(
        order=order,
        stage__in=['rider_to_customer', 'officer_to_customer'],
        used_at__isnull=False,
    ).order_by('-used_at').first()

    if handoff:
        deadline = handoff.used_at + timedelta(days=ReturnRequest.RETURN_WINDOW_DAYS)
        if timezone.now() > deadline:
            messages.error(
                request,
                f'The {ReturnRequest.RETURN_WINDOW_DAYS}-day return window for this order has passed.'
            )
            return redirect('my_orders')

    if ReturnRequest.objects.filter(order_item=item, status='requested').exists():
        messages.info(request, 'You already have a pending return request for this item.')
        return redirect('my_orders')

    if request.method == 'POST':
        form = ReturnRequestForm(request.POST, request.FILES)
        if form.is_valid():
            rr = form.save(commit=False)
            rr.order_item = item
            rr.customer = request.user
            rr.save()
            audit_log(request, 'return_requested', f'{item.product.name} (Order #{order.id})')
            messages.success(request, 'Your return request has been submitted. We will review it shortly.')
            return redirect('my_orders')
    else:
        form = ReturnRequestForm()

    return render(request, 'mall/request_return.html', {
        'item': item,
        'order': order,
        'form': form,
    })


@staff_member_required
def admin_returns(request):
    """List all return requests, newest first, filterable by status."""
    status = request.GET.get('status', '')
    returns = ReturnRequest.objects.select_related(
        'order_item__product', 'order_item__order', 'customer'
    ).order_by('-created')
    if status:
        returns = returns.filter(status=status)
    return render(request, 'mall/admin/returns.html', {
        'returns': returns,
        'status': status,
    })


@staff_member_required
def admin_return_decide(request, pk):
    """Approve or reject a pending return request."""
    rr = get_object_or_404(ReturnRequest, pk=pk)
    decision = (request.POST.get('decision') or '').strip()
    note = (request.POST.get('note') or '').strip()[:300]

    if decision not in ('approve', 'reject'):
        messages.error(request, 'Invalid decision.')
        return redirect('admin_returns')

    if rr.status != 'requested':
        messages.info(request, 'This return request has already been decided.')
        return redirect('admin_returns')

    rr.status = 'approved' if decision == 'approve' else 'rejected'
    rr.decision_note = note
    rr.decided_by = request.user
    rr.decided_at = timezone.now()
    rr.save()

    if decision == 'approve':
        title = '✅ Return approved'
        body = (
            f'Your return request for "{rr.order_item.product.name}" has been approved. '
            f'We will contact you about pickup.'
        )
    else:
        title = '❌ Return request update'
        body = f'Your return request for "{rr.order_item.product.name}" was not approved this time.'
    if note:
        body += f'\n\nNote: {note}'

    notify(
        rr.customer,
        notif_type='order_update',
        title=title,
        message=body,
        link='/my-orders/',
        whatsapp_text=body,
        sms_text=body[:150],
    )

    audit_log(request, f'return_{decision}d', f'{rr.order_item.product.name} — {rr.customer.username}')
    messages.success(request, 'Decision saved.')
    return redirect('admin_returns')


@staff_member_required
def admin_return_mark_received(request, pk):
    """Mark an approved return's item as physically received back."""
    rr = get_object_or_404(ReturnRequest, pk=pk)
    if rr.status != 'approved':
        messages.error(request, 'Only approved returns can be marked as received.')
        return redirect('admin_returns')
    def _dec(val):
        try:
            return Decimal(val) if val else Decimal('0')
        except Exception:
            return Decimal('0')

    rr.return_fee = _dec(request.POST.get('return_fee'))
    rr.waiting_fee = _dec(request.POST.get('waiting_fee'))
    rr.status = 'item_received'
    rr.save(update_fields=['status', 'updated', 'return_fee', 'waiting_fee'])
    credit_rider_for_rejection(rr)
    audit_log(request, 'return_item_received', f'{rr.order_item.product.name} — {rr.customer.username}')
    messages.success(request, 'Marked as item received.')
    return redirect('admin_returns')


@staff_member_required
def admin_return_refund(request, pk):
    """Record the refund amount and mark the return as refunded."""
    rr = get_object_or_404(ReturnRequest, pk=pk)
    if rr.status != 'item_received':
        messages.error(request, 'Item must be marked received before refunding.')
        return redirect('admin_returns')

    try:
        amount = Decimal(request.POST.get('refund_amount', ''))
    except (InvalidOperation, TypeError):
        messages.error(request, 'Enter a valid refund amount.')
        return redirect('admin_returns')

    if rr.buyer_fee_deduction > 0:
        amount = max(Decimal('0'), amount - rr.buyer_fee_deduction)

    rr.refund_amount = amount
    rr.refunded_at = timezone.now()
    rr.status = 'refunded'
    rr.save()

    audit_log(request, 'return_refunded', f'{rr.order_item.product.name} — GH₵{amount}')
    messages.success(request, 'Refund recorded.')
    return redirect('admin_returns')
