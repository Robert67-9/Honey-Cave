#!/usr/bin/env python3
"""
Fix rider payout in wallet.py to credit each seller-delivery's own
shipping_fee (per-seller, per-rider) instead of reading the single
flat order.shipping_fee field.

Uses an EXACT literal string match (no regex, no wildcards) copied
character-for-character from the real file content, and refuses to
touch anything if it doesn't match exactly once.

Run from the honey-cave project root:
    python3 fix_wallet_rider_payout_v3.py
"""

OLD = (
    "    # \u2500\u2500 Rider \u2014 paid from the delivery fee, home-delivery orders only \u2500\u2500\u2500\u2500\u2500\n"
    "    rider_delivery = order.seller_deliveries.first()\n"
    "    if rider_delivery and rider_delivery.rider and order.fulfillment_type == 'delivery':\n"
    "        gross = order.shipping_fee or Decimal('0')\n"
    "        if gross > 0:\n"
    "            commission = (gross * rider_rate).quantize(Decimal('0.01'))\n"
    "            net = gross - commission\n"
    "            wallet = get_or_create_rider_wallet(rider_delivery.rider)\n"
    "            _credit(\n"
    "                wallet, amount=net, tx_type='sale_credit', order=order,\n"
    "                note=(\n"
    "                    f'Delivery fee for order {order.order_number} \u2014 '\n"
    "                    f'GH\u20b5{gross} gross, {site.rider_commission_percent}% commission'\n"
    "                ),\n"
    "            )\n"
)

NEW = (
    "    # \u2500\u2500 Riders \u2014 paid per seller-delivery, from that delivery's own fee \u2500\u2500\u2500\n"
    "    # (Multiple RiderDelivery rows can exist per order \u2014 one per seller \u2014\n"
    "    # and may share the same rider or be split across different riders.\n"
    "    # Each row's shipping_fee is authoritative; order.shipping_fee is what\n"
    "    # the customer paid in total and is not used here.)\n"
    "    if order.fulfillment_type == 'delivery':\n"
    "        for delivery in order.seller_deliveries.select_related('rider').all():\n"
    "            if not delivery.rider:\n"
    "                continue\n"
    "            gross = delivery.shipping_fee or Decimal('0')\n"
    "            if gross <= 0:\n"
    "                continue\n"
    "            commission = (gross * rider_rate).quantize(Decimal('0.01'))\n"
    "            net = gross - commission\n"
    "            wallet = get_or_create_rider_wallet(delivery.rider)\n"
    "            _credit(\n"
    "                wallet, amount=net, tx_type='sale_credit', order=order,\n"
    "                note=(\n"
    "                    f'Delivery fee for order {order.order_number} \u2014 '\n"
    "                    f'GH\u20b5{gross} gross, {site.rider_commission_percent}% commission'\n"
    "                    + (f' (seller: {delivery.seller})' if delivery.seller_id else '')\n"
    "                ),\n"
    "            )\n"
)

def main():
    path = "mall/wallet.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    count = content.count(OLD)
    if count == 0:
        print(f"[WARN] {path}: exact snippet not found. No changes made. "
              f"Run: sed -n '128,145p' mall/wallet.py  and paste it back.")
        return
    if count > 1:
        print(f"[WARN] {path}: snippet matched {count} times, expected 1. No changes made.")
        return

    content = content.replace(OLD, NEW, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] {path}: patched (exact single-match replace)")

if __name__ == "__main__":
    main()
