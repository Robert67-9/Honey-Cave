"""
Paystack adapter. Paystack uses an inline pop-up flow:
  - Frontend opens the Paystack iframe with the public key and amount
  - Customer pays, Paystack redirects/calls back
  - Backend calls the verify endpoint to confirm the payment

initialize_payment is a no-op for inline mode (the frontend has the public
key and handles the pop-up itself). verify_payment delegates to the same
logic that's been in views.paystack_verify since the project started.
handle_webhook validates Paystack's HMAC-SHA512 signature and shapes the
result so the dispatcher can decide whether to mark an order paid.
"""
import hmac
import hashlib
import json
import logging

import requests
from django.conf import settings as django_settings
from django.utils.crypto import constant_time_compare

from .base import GatewayAdapter, InitResult, VerifyResult, WebhookResult

logger = logging.getLogger(__name__)


class PaystackAdapter(GatewayAdapter):
    provider_name = 'paystack'

    def initialize_payment(self, *, order, amount_pesewas, customer_email,
                           customer_phone, callback_url):
        """
        Paystack inline pop-up doesn't need server-side init in our setup —
        the JS opens the pop-up directly using the public key. We return
        the public key as the "authorization_url" so the checkout view can
        embed it in the page; the real network call happens at verify.
        """
        if not self.public_key:
            return InitResult(
                success=False,
                error_message='Paystack public key is not set. Add it under Panel → Payment Methods.',
            )
        return InitResult(
            success=True,
            authorization_url='',     # not used in inline mode
            reference='',             # frontend generates this
            raw={'public_key': self.public_key},
        )

    def verify_payment(self, reference):
        """Call Paystack's verify endpoint and parse the response."""
        secret_key = (getattr(django_settings, 'PAYSTACK_SECRET_KEY', '') or '').strip()
        public_key = self.public_key

        # Reject placeholders and missing keys with a clear, actionable error.
        if not secret_key:
            return VerifyResult(
                success=False,
                error_message='Paystack secret key is not configured. '
                              'Add PAYSTACK_SECRET_KEY to your .env file.',
            )
        if not secret_key.startswith(('sk_test_', 'sk_live_')):
            return VerifyResult(
                success=False,
                error_message='Paystack secret key is malformed. It must start with '
                              '"sk_test_" or "sk_live_". Check your .env file.',
            )

        # Detect test/live mode mismatch — the #1 cause of real-world verify
        # failures on live shops. Customer's payment succeeds with the public
        # key but the secret key can't find the transaction → 404.
        if public_key:
            pk_test = public_key.startswith('pk_test_')
            sk_test = secret_key.startswith('sk_test_')
            if pk_test != sk_test:
                return VerifyResult(
                    success=False,
                    error_message=(
                        'Payment gateway misconfigured: your Paystack public and secret '
                        'keys are from different modes (one is live, one is test). '
                        'Both must match. Please contact the store admin.'
                    ),
                )

        url = f'https://api.paystack.co/transaction/verify/{requests.utils.quote(reference, safe="")}'
        headers = {
            'Authorization': f'Bearer {secret_key}',
            'Content-Type':  'application/json',
            'Accept':        'application/json',
            'User-Agent':    'HoneyCaveMarket/1.0 (+https://honeycave.com)',
        }

        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as e:
            logger.exception('Paystack verify network error: %s', e)
            return VerifyResult(
                success=False,
                error_message='Could not reach Paystack — check your internet '
                              'connection and try again.',
            )

        if resp.status_code != 200:
            try:
                paystack_message = (resp.json() or {}).get('message', '') or ''
            except Exception:
                paystack_message = resp.text[:200]
            # Status-specific hints for the two most common deployment errors —
            # these unblock admins debugging "Verification failed" in production.
            if resp.status_code in (401, 403):
                user_msg = (
                    f'Paystack rejected the secret key (HTTP {resp.status_code}). '
                    f'Check that PAYSTACK_SECRET_KEY in your .env file is correct '
                    f'and not expired.'
                )
            elif resp.status_code == 404:
                user_msg = (
                    'Paystack says this transaction reference was not found '
                    '(HTTP 404). This usually means the public key and secret '
                    'key are from different Paystack accounts or modes (test vs live).'
                )
            else:
                user_msg = f'Paystack verification failed (HTTP {resp.status_code}).'
            if paystack_message:
                user_msg = f'{user_msg} Paystack says: {paystack_message}'
            logger.error(
                'Paystack verify HTTP %s for ref=%s — body=%s',
                resp.status_code, reference, (resp.text or '')[:500],
            )
            return VerifyResult(success=False, error_message=user_msg)

        payload = resp.json()
        data = payload.get('data') or {}
        is_paid = (
            payload.get('status') is True
            and (data.get('status') == 'success')
        )
        return VerifyResult(
            success=True,
            is_paid=is_paid,
            amount_pesewas=int(data.get('amount') or 0),
            currency=data.get('currency') or 'GHS',
            customer_email=(data.get('customer') or {}).get('email', ''),
            customer_phone='',  # Paystack doesn't always return this
            error_message='' if is_paid else (
                data.get('gateway_response') or
                payload.get('message') or 'Payment was not successful.'
            ),
            raw=payload,
        )

    def handle_webhook(self, request):
        """
        Verify a Paystack webhook and extract the payment outcome.

        Paystack signs every webhook with HMAC-SHA512 using your secret
        key (NOT the public key). The signature lives in the
        X-Paystack-Signature header. We verify before parsing.

        Returns a WebhookResult so the dispatcher can:
          - reject invalid signatures with HTTP 400
          - mark the matching order paid if event=charge.success
          - log + acknowledge other events without action
        """
        secret = (getattr(django_settings, 'PAYSTACK_SECRET_KEY', '') or '').strip()
        if not secret:
            return WebhookResult(
                success=False,
                http_status=500,
                response_body='Paystack not configured.',
                error_message='PAYSTACK_SECRET_KEY missing',
            )

        sig_header = request.headers.get('X-Paystack-Signature', '')
        expected = hmac.new(
            secret.encode('utf-8'), request.body, hashlib.sha512,
        ).hexdigest()
        if not constant_time_compare(sig_header, expected):
            logger.warning('Paystack webhook: invalid signature')
            return WebhookResult(
                success=False,
                http_status=400,
                response_body='Invalid signature.',
                error_message='Signature mismatch',
            )

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError as e:
            return WebhookResult(
                success=False,
                http_status=400,
                response_body='Invalid JSON.',
                error_message=str(e),
            )

        event = payload.get('event', '')
        data  = payload.get('data', {}) or {}

        if event == 'charge.success':
            reference = (data.get('reference') or '').strip()
            amount    = int(data.get('amount') or 0)   # pesewas
            if not reference:
                return WebhookResult(
                    success=True,
                    is_payment_event=False,
                    event_type=event,
                    response_body='ignored — no reference',
                    raw=payload,
                )
            return WebhookResult(
                success=True,
                is_payment_event=True,
                reference=reference,
                amount_pesewas=amount,
                event_type=event,
                response_body='ok',
                raw=payload,
            )

        # Non-payment event (transfer.success, etc.) — acknowledge so
        # Paystack stops retrying, but don't update any orders.
        return WebhookResult(
            success=True,
            is_payment_event=False,
            event_type=event,
            response_body='ok',
            raw=payload,
        )
