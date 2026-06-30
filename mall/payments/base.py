"""
Payment gateway adapter interface — common shape every provider implements.

Why an adapter pattern: the checkout flow shouldn't care which gateway is
running. It just calls `gateway.initialize_payment(...)` and gets back a
we add a new adapter file in this folder — no changes to checkout code.

To add a new provider:
  1. Subclass GatewayAdapter (see paystack.py for the simplest reference)
  2. Implement initialize_payment() and verify_payment()
  3. Register the class in dispatch.py:GATEWAY_REGISTRY
  4. Add the provider's slug to PaymentSettings.PROVIDER_CHOICES in models.py
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class InitResult:
    """Result returned by initialize_payment().

    success — True if the gateway accepted the request and returned an
              authorization URL or pop-up token. False on misconfiguration
              or upstream error.

    authorization_url — URL the browser should open (Paystack inline pop-up

    reference         — gateway-side payment reference. Stored on the order
                        and used later by verify_payment().

    error_message     — empty when success=True, otherwise a human-readable
                        explanation suitable for showing the customer.

    raw               — the unparsed JSON response from the gateway, for
                        debugging and logs.
    """
    success: bool
    authorization_url: str = ''
    reference: str = ''
    error_message: str = ''
    raw: dict = None


@dataclass
class VerifyResult:
    """Result returned by verify_payment()."""
    success: bool
    is_paid: bool = False              # True if the payment was successful
    amount_pesewas: int = 0            # amount actually paid, in pesewas (not cedis)
    currency: str = 'GHS'
    customer_email: str = ''
    customer_phone: str = ''
    error_message: str = ''
    raw: dict = None


@dataclass
class WebhookResult:
    """
    Result returned by handle_webhook(). The dispatcher uses this to:
      - know whether the signature was valid (otherwise reject the request)
      - know whether to look up an order and mark it paid
      - shape the HTTP response back to the gateway

    A successful webhook for a payment event sets `success=True` plus
    `reference` and `amount_pesewas`. The dispatcher then handles order
    lookup and notification; adapters don't need to know about Order.
    """
    success: bool
    is_payment_event: bool = False    # True only for charge.success-style events
    reference: str = ''
    amount_pesewas: int = 0
    event_type: str = ''              # gateway-specific event slug for logs
    http_status: int = 200            # response status the dispatcher should return
    response_body: str = 'ok'
    error_message: str = ''
    raw: dict = None


class GatewayAdapter(ABC):
    """
    Base class. Concrete adapters must implement initialize_payment() and
    verify_payment(). All other behaviour (logging, retries) is provided
    by helpers in this module.
    """
    provider_name = 'unknown'

    # Set to False on stub/incomplete adapters so the storefront can hide
    # them from the checkout payment-method picker even when their
    # PaymentSettings row has is_active=True. Prevents customers picking
    # a gateway that will fail at init time.
    is_ready = True

    def __init__(self, settings):
        """`settings` is a PaymentSettings model instance for this gateway."""
        self.settings = settings

    @abstractmethod
    def initialize_payment(self, *, order, amount_pesewas, customer_email,
                           customer_phone, callback_url):
        """Return an InitResult."""
        raise NotImplementedError

    @abstractmethod
    def verify_payment(self, reference):
        """Given a gateway reference, ask the gateway whether it succeeded.
        Return a VerifyResult."""
        raise NotImplementedError

    def handle_webhook(self, request):
        """
        Parse, signature-verify, and interpret a webhook from this gateway.

        Default implementation rejects the request — concrete adapters
        override this when they support webhooks. Returns a WebhookResult
        the dispatcher uses to drive order updates.
        """
        return WebhookResult(
            success=False,
            http_status=501,
            response_body='Webhook not implemented for this gateway.',
            error_message=f'{self.provider_name} adapter has no handle_webhook().',
        )

    @property
    def public_key(self):
        return (self.settings.account_number or '').strip()

    @property
    def account_number(self):
        return (self.settings.extra_account or '').strip()
