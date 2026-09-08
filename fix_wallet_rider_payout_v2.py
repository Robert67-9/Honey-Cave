#!/usr/bin/env python3
"""
Fix rider payout in wallet.py to credit each seller-delivery's own
shipping_fee (per-seller, per-rider) instead of reading the single
flat order.shipping_fee field.

Uses a regex anchored only on the ASCII code structure (with wildcards
for the decorative unicode dash characters in comments/notes), so it's
robust to exactly which dash character the file actually uses.

Run from the honey-cave project root:
    python3 fix_wallet_rider_payout_v2.py
"""
import re

PATTERN = re.compile(
    r"    # .*?\n"
    r"    rider_delivery = order\.seller_deliveries\.first\(\)\n"
    r"    if rider_delivery and rider_delivery\.rider and order\.fulfillment_type == 'delivery':\n"
    r"        gross = order\.shipping_fee or Decimal\('0'\)\n"
    r"        if gross > 0:\n"
    r"            commission = \(gross \* rider_rate\)\.quantize\(Decimal\('0\.01'\)\)\n"
    r"            net = gross - commission\n"
    r"            wallet = get_or_create_rider_wallet\(rider_delivery\.rider\)\n"
    r"            _credit\(\n"
    r"                wallet, amount=net, tx_type='sale_credit', order=order,\n"
    r"                note=\(\n"
    r"                    f'Delivery fee for order \{order\.order_number\}.*?'\n"
    r"                    f'GH.*?\{gross\} gross, \{site\.rider_commission_percent\}% commission'\n"
    r"                \),\n"
    r"            \)\n",
    re.DOTALL,
)

REPLACEMENT = (
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

    matches = PATTERN.findall(content)
    count = len(matches)
    if count == 0:
        print(f"[WARN] {path}: pattern not found -- please paste back `sed -n '125,145p' mall/wallet.py` so I can see exactly what's there.")
        return
    if count > 1:
        print(f"[WARN] {path}: pattern matched {count} times, expected 1 -- not applying, please review manually.")
        return

    content = PATTERN.sub(lambda m: REPLACEMENT, content, count=1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] {path}: patched")

if __name__ == "__main__":
    main()
