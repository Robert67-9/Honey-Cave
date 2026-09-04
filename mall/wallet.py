"""
Wallet service layer for Honey Cave Market.

This module is the ONLY place that should mutate Wallet.pending_balance /
available_balance / withdrawn_total / reserve_held. Every change is first
written as a WalletTransaction ledger row, then the cached balance columns
on Wallet are updated to match — never edit those columns directly
anywhere else (views, admin, shell).

Called from:
  - mall/handoff.py  → credit_order_earnings() when a delivery is confirmed
    (officer_to_customer / rider_to_customer handoff stage verified)
  - a management command (release_matured_earnings) → run on a cron/
    scheduled task to flip pending → available once the hold expires
  - mall/views.py (seller) and mall/rider_views.py (rider) → withdrawal
    request + OTP confirm + Paystack transfer flow
  - mall/payments/paystack.py webhook dispatcher → mark a withdrawal
    completed/failed once Paystack confirms the transfer
"""
import logging
from collections import defaultdict
from decimal import Decimal
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    Wallet, WalletTransaction, WithdrawalRequest, SiteSettings,
)

logger = logging.getLogger(__name__)


class WalletError(Exception):
    """Raised for any wallet/withdrawal rule violation — caller should show
    str(e) to the user as a plain error message."""
    pass


# ─── Wallet lookup ──────────────────────────────────────────────────────────

def get_or_create_seller_wallet(user):
    wallet, _ = Wallet.objects.get_or_create(
        seller=user, owner_type='seller',
    )
    return wallet


def get_or_create_rider_wallet(rider):
    wallet, _ = Wallet.objects.get_or_create(
        rider=rider, owner_type='rider',
        defaults={'is_trusted': rider.is_verified},
    )
    return wallet


# ─── Crediting earnings ─────────────────────────────────────────────────────

@transaction.atomic
def _credit(wallet, *, amount, tx_type, order=None, note=''):
    """Write a pending ledger credit and bump wallet.pending_balance to match."""
    if amount <= 0:
        return None
    hold_hours = wallet.hold_hours()
    available_at = timezone.now() + timedelta(hours=hold_hours)
    tx = WalletTransaction.objects.create(
        wallet=wallet, order=order, type=tx_type, status='pending',
        amount=amount, note=note, available_at=available_at,
    )
    Wallet.objects.filter(pk=wallet.pk).update(
        pending_balance=F('pending_balance') + amount,
    )
    return tx


@transaction.atomic
def credit_order_earnings(order):
    """
    Called once, when an order's delivery is confirmed (handoff.py calls
    this from advance_after_verify on the officer_to_customer / rider_to_customer
    stage). Credits:

      - each item's seller (Product.created_by) their share of the item
        revenue, minus the platform's seller commission
      - the assigned rider their share of the order's delivery fee, minus
        the platform's rider commission (home-delivery orders only —
        pickup orders have no rider)

    Idempotent: safe to call more than once for the same order — it skips
    if a sale_credit row already exists for this order, so a retried
    webhook or a re-run handoff step can never double-pay anyone.
    """
    if WalletTransaction.objects.filter(order=order, type='sale_credit').exists():
        return

    site = SiteSettings.load()
    seller_rate = site.seller_commission_percent / Decimal('100')
    rider_rate = site.rider_commission_percent / Decimal('100')
    reserve_rate = site.reserve_percent / Decimal('100')

    # ── Sellers — one payout per distinct product owner on this order ──────
    totals_by_seller = defaultdict(Decimal)
    for item in order.items.select_related('product__created_by'):
        seller = item.product.created_by
        if not seller or seller.is_superuser:
            continue  # platform-owned product (no created_by) — no seller payout
        totals_by_seller[seller] += item.get_total_price()

    for seller, gross in totals_by_seller.items():
        commission = (gross * seller_rate).quantize(Decimal('0.01'))
        reserve = (gross * reserve_rate).quantize(Decimal('0.01'))
        net = gross - commission - reserve
        wallet = get_or_create_seller_wallet(seller)
        _credit(
            wallet, amount=net, tx_type='sale_credit', order=order,
            note=(
                f'Order {order.order_number} — GH₵{gross} gross, '
                f'{site.seller_commission_percent}% commission, '
                f'{site.reserve_percent}% reserve held'
            ),
        )
        if reserve > 0:
            WalletTransaction.objects.create(
                wallet=wallet, order=order, type='reserve_hold', status='available',
                amount=reserve, note=f'Reserve held from order {order.order_number}',
            )
            Wallet.objects.filter(pk=wallet.pk).update(reserve_held=F('reserve_held') + reserve)

    # ── Rider — paid from the delivery fee, home-delivery orders only ─────
    rider_delivery = getattr(order, 'rider_delivery', None)
    if rider_delivery and rider_delivery.rider and order.fulfillment_type == 'delivery':
        gross = order.shipping_fee or Decimal('0')
        if gross > 0:
            commission = (gross * rider_rate).quantize(Decimal('0.01'))
            net = gross - commission
            wallet = get_or_create_rider_wallet(rider_delivery.rider)
            _credit(
                wallet, amount=net, tx_type='sale_credit', order=order,
                note=(
                    f'Delivery fee for order {order.order_number} — '
                    f'GH₵{gross} gross, {site.rider_commission_percent}% commission'
                ),
            )


# ─── Releasing matured holds (pending → available) ─────────────────────────

@transaction.atomic
def release_matured_pending(wallet):
    """
    Flip any of this wallet's pending WalletTransaction rows whose hold
    window has passed into 'available', and move the amount from
    pending_balance to available_balance. Orders that were cancelled after
    the credit was issued are skipped (and left pending) — an admin must
    resolve those manually via a reversal.
    """
    now = timezone.now()
    matured = wallet.transactions.filter(status='pending', available_at__lte=now)
    total = Decimal('0')
    for tx in matured.select_related('order'):
        if tx.order_id and tx.order.status == 'cancelled':
            continue  # leave pending — admin must review/reverse
        tx.status = 'available'
        tx.save(update_fields=['status'])
        total += tx.amount
    if total:
        Wallet.objects.filter(pk=wallet.pk).update(
            pending_balance=F('pending_balance') - total,
            available_balance=F('available_balance') + total,
        )
    return total


def release_all_matured_pending():
    """Run across every wallet — call this from a daily/hourly cron task."""
    released = Decimal('0')
    for wallet in Wallet.objects.all():
        released += release_matured_pending(wallet)
    return released


# ─── Withdrawals ────────────────────────────────────────────────────────────

def request_withdrawal(wallet, *, amount, method, destination):
    """
    Validate and create a WithdrawalRequest in 'pending_otp' state. Does
    NOT move any money yet — that only happens once the OTP is confirmed
    (see confirm_withdrawal_otp below), so a request that's never
    confirmed just expires harmlessly.

    `destination` is a dict of whichever of these apply:
      bank: {'bank_name', 'account_number', 'account_name'}
      momo: {'momo_network', 'account_number' (momo number), 'account_name'}
    """
    site = SiteSettings.load()

    if wallet.is_frozen:
        raise WalletError('This wallet is frozen pending a review. Please contact support.')
    if amount < site.withdrawal_min_amount:
        raise WalletError(f'Minimum withdrawal is GH₵{site.withdrawal_min_amount}.')
    if amount > wallet.available_balance:
        raise WalletError('That amount exceeds your available balance.')
    if method not in ('bank', 'momo'):
        raise WalletError('Choose a valid payout method.')

    fee = Decimal('0') if wallet.fee_waived else site.withdrawal_fee
    net = amount - fee
    if net <= 0:
        raise WalletError('Amount is too small to cover the withdrawal fee.')

    wr = WithdrawalRequest.objects.create(
        wallet=wallet, amount=amount, fee_charged=fee, net_amount=net,
        method=method,
        bank_name=destination.get('bank_name', ''),
        account_number=destination.get('account_number', ''),
        account_name=destination.get('account_name', ''),
        momo_network=destination.get('momo_network', ''),
    )
    return wr


@transaction.atomic
def confirm_withdrawal_otp(withdrawal):
    """
    Called once the OTP has been verified by the caller (seller flow uses
    OTPVerification, rider flow uses RiderOTP — both checked by the view
    before calling this). Moves the requested amount out of
    available_balance immediately (so it can't be double-spent by a second
    withdrawal request while the transfer is in flight), then hands off to
    Paystack.
    """
    wallet = withdrawal.wallet
    if withdrawal.status != 'pending_otp':
        raise WalletError('This withdrawal has already been processed.')
    if withdrawal.amount > wallet.available_balance:
        raise WalletError('Available balance has changed — please request again.')

    withdrawal.otp_verified = True
    withdrawal.otp_verified_at = timezone.now()
    withdrawal.status = 'processing'
    withdrawal.save(update_fields=['otp_verified', 'otp_verified_at', 'status'])

    Wallet.objects.filter(pk=wallet.pk).update(
        available_balance=F('available_balance') - withdrawal.amount,
    )
    WalletTransaction.objects.create(
        wallet=wallet, withdrawal=withdrawal, type='withdrawal_debit',
        status='withdrawn', amount=-withdrawal.net_amount,
        note=f'Withdrawal {withdrawal.unique_reference} — GH₵{withdrawal.amount} requested',
    )
    if withdrawal.fee_charged:
        WalletTransaction.objects.create(
            wallet=wallet, withdrawal=withdrawal, type='withdrawal_fee',
            status='withdrawn', amount=-withdrawal.fee_charged,
            note=f'Withdrawal fee for {withdrawal.unique_reference}',
        )

    from .payments.transfers import initiate_paystack_transfer
    try:
        initiate_paystack_transfer(withdrawal)
    except Exception as e:
        logger.exception('Paystack transfer initiation failed for %s: %s', withdrawal.unique_reference, e)
        reverse_withdrawal(withdrawal, reason=f'Transfer initiation error: {e}')

    return withdrawal


@transaction.atomic
def reverse_withdrawal(withdrawal, *, reason=''):
    """
    Undo a withdrawal that failed at or after the Paystack transfer step —
    returns the gross amount to available_balance and marks the request
    'failed' (not 'reversed'; 'reversed' is reserved for a transfer that
    Paystack initially reported success on but later reversed).
    """
    wallet = withdrawal.wallet
    if withdrawal.status in ('completed',):
        raise WalletError('Cannot reverse a completed withdrawal this way — use an admin adjustment.')

    withdrawal.status = 'failed'
    withdrawal.failure_reason = reason[:300]
    withdrawal.processed_at = timezone.now()
    withdrawal.save(update_fields=['status', 'failure_reason', 'processed_at'])

    Wallet.objects.filter(pk=wallet.pk).update(
        available_balance=F('available_balance') + withdrawal.amount,
    )
    WalletTransaction.objects.create(
        wallet=wallet, withdrawal=withdrawal, type='reversal', status='available',
        amount=withdrawal.amount,
        note=f'Reversed failed withdrawal {withdrawal.unique_reference}: {reason}'[:300],
    )


def expire_stale_pending_otp(max_age_minutes=30):
    """
    A withdrawal stuck in 'pending_otp' past this age almost always means
    the OTP was never confirmed — the seller/rider lost their session, the
    code never arrived, or they gave up. No money has moved yet at this
    stage (confirm_withdrawal_otp is what debits available_balance), so
    this is a pure cleanup: mark it 'failed' with a clear reason, freeing
    them to submit a fresh request instead of the old one sitting there
    forever looking like something is broken.

    Run this periodically (same cron/scheduled task as
    release_matured_earnings). Returns the number expired.
    """
    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    stale = WithdrawalRequest.objects.filter(status='pending_otp', requested_at__lt=cutoff)
    count = stale.update(
        status='failed',
        failure_reason='OTP confirmation window expired — no code was confirmed in time.',
        processed_at=timezone.now(),
    )
    return count


def find_resumable_withdrawal(wallet, max_age_minutes=30):
    """
    An existing 'pending_otp' request for this wallet, still within the
    resend window — used by the withdraw views to avoid creating a
    duplicate stuck request every time someone clicks "Cash Out" again
    after losing their session or not receiving the OTP.
    """
    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    return (WithdrawalRequest.objects
            .filter(wallet=wallet, status='pending_otp', requested_at__gte=cutoff)
            .order_by('-requested_at')
            .first())


@transaction.atomic
def mark_withdrawal_completed(withdrawal):
    """Called from the Paystack transfer.success webhook handler."""
    if withdrawal.status == 'completed':
        return  # already processed — webhook retried
    withdrawal.status = 'completed'
    withdrawal.processed_at = timezone.now()
    withdrawal.save(update_fields=['status', 'processed_at'])
    Wallet.objects.filter(pk=withdrawal.wallet_id).update(
        withdrawn_total=F('withdrawn_total') + withdrawal.amount,
    )


# ─── Product boosts ─────────────────────────────────────────────────────────

def activate_boost(boost):
    """
    Flip a paid-for ProductBoost to 'active' and set its live window. Called
    once payment is confirmed — either immediately (paid from wallet) or
    from the Paystack verify view (paid by card).
    """
    from datetime import timedelta as _td
    from .models import ProductBoost
    now = timezone.now()
    boost.status = 'active'
    boost.starts_at = now
    boost.ends_at = now + _td(days=ProductBoost.TIER_DAYS[boost.tier])
    boost.save(update_fields=['status', 'starts_at', 'ends_at'])


@transaction.atomic
def purchase_boost_from_wallet(wallet, boost):
    """
    Pay for a ProductBoost out of the seller's own available_balance instead
    of a fresh card charge — convenient for sellers who already have
    earnings sitting in their wallet. Debits immediately and activates the
    boost in the same step (no async confirmation needed, unlike a
    Paystack charge).
    """
    if wallet.available_balance < boost.amount:
        raise WalletError('Not enough available balance to cover this boost — top up or pay by card instead.')

    Wallet.objects.filter(pk=wallet.pk).update(
        available_balance=F('available_balance') - boost.amount,
    )
    WalletTransaction.objects.create(
        wallet=wallet, type='boost_debit', status='available',
        amount=-boost.amount,
        note=f'Boost ({boost.get_tier_display()}) for "{boost.product.name}", paid from wallet',
    )
    boost.payment_reference = f'WALLET-{boost.id}'
    boost.save(update_fields=['payment_reference'])
    activate_boost(boost)
    return boost
