"""
Gateway dispatcher — picks the right adapter for a PaymentSettings row.

Usage from a view:
    from mall.payments import dispatch
    gateway = dispatch.gateway_for(payment_settings_obj)
    result = gateway.verify_payment(reference)

If the provider slug isn't registered, returns a NullGateway that fails
clean rather than crashing — useful when the database has a row from a
deleted provider.
"""
from .base import GatewayAdapter, InitResult, VerifyResult
from .paystack import PaystackAdapter


GATEWAY_REGISTRY = {
    'paystack': PaystackAdapter,
}


class NullGateway(GatewayAdapter):
    """Fallback when the provider slug isn't recognised."""
    provider_name = 'unknown'

    def initialize_payment(self, *, order, amount_pesewas, customer_email,
                           customer_phone, callback_url):
        return InitResult(
            success=False,
            error_message=f'Unknown payment provider: {self.settings.provider!r}',
        )

    def verify_payment(self, reference):
        return VerifyResult(
            success=False,
            error_message=f'Unknown payment provider: {self.settings.provider!r}',
        )


def gateway_for(payment_settings):
    """Return an adapter instance for the given PaymentSettings row."""
    cls = GATEWAY_REGISTRY.get(payment_settings.provider, NullGateway)
    return cls(payment_settings)


def gateway_by_provider(provider_slug):
    """
    Return (adapter, payment_settings) for the first PaymentSettings row
    matching `provider_slug`, regardless of is_active. Used by webhook
    handlers — a webhook can arrive after a row has been deactivated, and
    we still want to verify the signature and acknowledge it cleanly.

    Returns (None, None) if no row exists for that provider.
    """
    from ..models import PaymentSettings
    ps = (PaymentSettings.objects
          .filter(provider=provider_slug)
          .order_by('-is_active', '-updated_at')
          .first())
    if ps is None:
        return None, None
    return gateway_for(ps), ps


def get_active_gateway(prefer_provider=None):
    """
    Convenience: load the first active PaymentSettings row and return its
    gateway adapter. If `prefer_provider` is set, prefer that one.

    Returns (adapter, payment_settings_instance) or (None, None) if no
    active gateway exists.
    """
    from ..models import PaymentSettings
    qs = PaymentSettings.objects.filter(is_active=True)
    if prefer_provider:
        ps = qs.filter(provider=prefer_provider).first()
        if ps:
            return gateway_for(ps), ps
    ps = qs.first()
    if ps is None:
        return None, None
    return gateway_for(ps), ps
