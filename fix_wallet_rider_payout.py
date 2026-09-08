#!/usr/bin/env python3
"""
Fix rider payout in wallet.py to credit each seller-delivery's own
shipping_fee (per-seller, per-rider) instead of reading the single
flat order.shipping_fee field.

Run from the honey-cave project root:
    python3 fix_wallet_rider_payout.py
"""

OLD = """    # \u2500\u2500 Rider \u2500 paid from the delivery fee, home-delivery orders only \u2500\u2500\u2500\u2500\u2500
    rider_delivery = order.seller_deliveries.first()
    if rider_delivery and rider_delivery.rider and order.fulfillment_type == 'delivery':
        gross = order.shipping_fee or Decimal('0')
        if gross > 0:
            commission = (gross * rider_rate).quantize(Decimal('0.01'))
            net = gross - commission
            wallet = get_or_create_rider_wallet(rider_delivery.rider)
            _credit(
                wallet, amount=net, tx_type='sale_credit', order=order,
                note=(
                    f'Delivery fee for order {order.order_number} \u2500 '
                    f'GH\u20b5{gross} gross, {site.rider_commission_percent}% commission'
                ),
            )
"""

NEW = """    # \u2500\u2500 Riders \u2500 paid per seller-delivery, from that delivery's own fee \u2500\u2500\u2500
    # (Multiple RiderDelivery rows can exist per order \u2500 one per seller \u2500
    # and may share the same rider or be split across different riders.
    # Each row's shipping_fee is authoritative; order.shipping_fee is what
    # the customer paid in total and is not used here.)
    if order.fulfillment_type == 'delivery':
        for delivery in order.seller_deliveries.select_related('rider').all():
            if not delivery.rider:
                continue
            gross = delivery.shipping_fee or Decimal('0')
            if gross <= 0:
                continue
            commission = (gross * rider_rate).quantize(Decimal('0.01'))
            net = gross - commission
            wallet = get_or_create_rider_wallet(delivery.rider)
            _credit(
                wallet, amount=net, tx_type='sale_credit', order=order,
                note=(
                    f'Delivery fee for order {order.order_number} \u2500 '
                    f'GH\u20b5{gross} gross, {site.rider_commission_percent}% commission'
                    + (f' (seller: {delivery.seller})' if delivery.seller_id else '')
                ),
            )
"""

def main():
    path = "mall/wallet.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    count = content.count(OLD)
    if count == 0:
        print(f"[WARN] {path}: expected snippet not found -- already patched, or context drifted. No changes made.")
        return
    if count > 1:
        print(f"[WARN] {path}: snippet matched {count} times, expected 1 -- not applying, please review manually.")
        return

    content = content.replace(OLD, NEW)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] {path}: patched")

if __name__ == "__main__":
    main()
