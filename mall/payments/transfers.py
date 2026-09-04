"""
Paystack Transfer API — pays out seller/rider withdrawals to a Ghanaian
bank account or mobile money wallet.

Flow:
  1. create_recipient()     — register the destination with Paystack, get
                              back a recipient_code (bank or mobile_money).
  2. initiate_paystack_transfer() — start the transfer using that
                              recipient_code + our unique_reference.
  3. Paystack sends a transfer.success / transfer.failed / transfer.reversed
     webhook (handled in mall/payments/paystack.py + the dispatcher in
     mall/views.py) — that webhook is the ONLY place a withdrawal is
     marked 'completed'. The synchronous response from step 2 only tells
     us Paystack *accepted* the request, not that money actually moved.

Ghana mobile money networks map to Paystack's `mobile_money` recipient
bank codes:
    MTN        → MTN
    Telecel    → VOD  (Paystack still uses the old "Vodafone" code)
    AirtelTigo → ATL
"""
import logging

import requests
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)

MOMO_BANK_CODES = {
    'MTN': 'MTN',
    'TELECEL': 'VOD',
    'AIRTELTIGO': 'ATL',
}

# Common Ghanaian banks and their Paystack bank codes, for the withdrawal
# form's bank picker. WithdrawalRequest.bank_name stores the CODE (e.g.
# '030100'), not the display name — the view/template pairs it back up
# with this dict for display. Not exhaustive; extend as needed.
GHANA_BANKS = [
    ('GH210100', 'GCB Bank'),
    ('GH130100', 'Ecobank Ghana'),
    ('GH140100', 'Fidelity Bank'),
    ('GH070100', 'Absa Bank Ghana'),
    ('GH060100', 'Stanbic Bank'),
    ('GH240100', 'Zenith Bank Ghana'),
    ('GH080100', 'Standard Chartered Bank Ghana'),
    ('GH300100', 'CalBank'),
    ('GH190100', 'Access Bank Ghana'),
    ('GH250100', 'Consolidated Bank Ghana'),
    ('GH170100', 'Republic Bank Ghana'),
    ('GH400100', 'United Bank for Africa Ghana'),
]

PAYSTACK_BASE = 'https://api.paystack.co'


class TransferError(Exception):
    pass


def _secret_key():
    key = (getattr(django_settings, 'PAYSTACK_SECRET_KEY', '') or '').strip()
    if not key:
        raise TransferError('Paystack secret key is not configured.')
    return key


def _headers():
    return {
        'Authorization': f'Bearer {_secret_key()}',
        'Content-Type': 'application/json',
        'User-Agent': 'HoneyCaveMarket/1.0 (+https://honeycave.com)',
    }


def create_recipient(withdrawal):
    """
    Register the withdrawal's destination with Paystack and return a
    recipient_code. Called once per withdrawal (recipients aren't reused
    across requests, since a seller/rider can change their payout details
    between withdrawals).
    """
    if withdrawal.method == 'momo':
        bank_code = MOMO_BANK_CODES.get((withdrawal.momo_network or '').upper())
        if not bank_code:
            raise TransferError(f'Unsupported mobile money network: {withdrawal.momo_network}')
        payload = {
            'type': 'mobile_money',
            'name': withdrawal.account_name or withdrawal.wallet.owner_label,
            'account_number': withdrawal.account_number,
            'bank_code': bank_code,
            'currency': 'GHS',
        }
    else:
        payload = {
            'type': 'ghipss',  # Ghana bank transfer recipient type
            'name': withdrawal.account_name or withdrawal.wallet.owner_label,
            'account_number': withdrawal.account_number,
            'bank_code': withdrawal.bank_name,  # expects the Paystack bank code, set via a bank picker
            'currency': 'GHS',
        }

    try:
        resp = requests.post(
            f'{PAYSTACK_BASE}/transferrecipient',
            json=payload, headers=_headers(), timeout=15,
        )
    except requests.RequestException as e:
        raise TransferError(f'Could not reach Paystack: {e}')

    body = resp.json() if resp.content else {}
    if resp.status_code not in (200, 201) or not body.get('status'):
        message = body.get('message', f'HTTP {resp.status_code}')
        raise TransferError(f'Paystack rejected the payout destination: {message}')

    return body['data']['recipient_code']


def initiate_paystack_transfer(withdrawal):
    """
    Create the recipient (if needed) and start the transfer. Updates the
    withdrawal row with the Paystack codes/status but does NOT mark it
    completed — only the webhook does that.
    """
    if not withdrawal.paystack_recipient_code:
        recipient_code = create_recipient(withdrawal)
        withdrawal.paystack_recipient_code = recipient_code
        withdrawal.save(update_fields=['paystack_recipient_code'])

    payload = {
        'source': 'balance',
        'amount': int(withdrawal.net_amount * 100),  # pesewas
        'recipient': withdrawal.paystack_recipient_code,
        'reference': withdrawal.unique_reference,
        'reason': f'Honey Cave Market payout — {withdrawal.wallet.owner_label}',
    }

    try:
        resp = requests.post(
            f'{PAYSTACK_BASE}/transfer',
            json=payload, headers=_headers(), timeout=15,
        )
    except requests.RequestException as e:
        raise TransferError(f'Could not reach Paystack: {e}')

    body = resp.json() if resp.content else {}
    if resp.status_code not in (200, 201) or not body.get('status'):
        message = body.get('message', f'HTTP {resp.status_code}')
        raise TransferError(f'Paystack rejected the transfer: {message}')

    data = body.get('data') or {}
    withdrawal.paystack_transfer_code = data.get('transfer_code', '')
    withdrawal.paystack_status = data.get('status', '')
    withdrawal.save(update_fields=['paystack_transfer_code', 'paystack_status'])

    # Paystack can (rarely) resolve a transfer synchronously as 'success'
    # if OTP-on-transfer is disabled on the account. Handle that here too,
    # since the webhook might already be a duplicate by the time it lands.
    if data.get('status') == 'success':
        from ..wallet import mark_withdrawal_completed
        mark_withdrawal_completed(withdrawal)

    return withdrawal
