#!/usr/bin/env python3
"""
One-shot fix for the RiderDelivery related_name migration
(rider_delivery -> seller_deliveries, OneToOne -> ForeignKey).

Run from the honey-cave project root (where mall/ lives):
    python3 fix_rider_delivery.py

It's safe to re-run: if a file's OLD string is no longer found (because
it was already patched), that file is skipped with a note instead of
silently doing nothing wrong.
"""

import sys

# Each entry: (filepath, [(old, new), ...])
FIXES = [
    (
        "mall/fulfillment_officer_views.py",
        [
            (
                ".select_related('branch', 'rider_delivery')",
                ".select_related('branch').prefetch_related('seller_deliveries')",
            ),
            (
                "            # Create or update the delivery row\n"
                "            try:\n"
                "                existing_delivery = order.rider_delivery\n"
                "            except Exception:\n"
                "                existing_delivery = None\n",
                "            # Create or update the delivery row\n"
                "            existing_delivery = order.seller_deliveries.first()\n",
            ),
            (
                "    codes_by_stage = _codes_by_stage(order)\n"
                "    # OneToOneField reverse lookup raises if no related object exists,\n"
                "    # so wrap in try/except. The attribute hasattr() check works too.\n"
                "    try:\n"
                "        rider_delivery = order.rider_delivery\n"
                "    except Exception:\n"
                "        rider_delivery = None\n",
                "    codes_by_stage = _codes_by_stage(order)\n"
                "    # ForeignKey reverse (order -> seller_deliveries): .first() is safe,\n"
                "    # never raises. NOTE: once multi-seller dispatch ships, this must pick\n"
                "    # a specific seller's delivery, not just the first one.\n"
                "    rider_delivery = order.seller_deliveries.first()\n",
            ),
        ],
    ),
    (
        "mall/views.py",
        [
            (
                "    rider_delivery_obj = None\n"
                "    try:\n"
                "        rider_delivery_obj = order.rider_delivery\n"
                "    except Exception:\n"
                "        pass\n",
                "    rider_delivery_obj = order.seller_deliveries.first()\n",
            ),
            (
                "    rider = getattr(order, 'rider_delivery', None)\n\n"
                "    # Pick the right code based on fulfillment type",
                "    rider = order.seller_deliveries.first()\n\n"
                "    # Pick the right code based on fulfillment type",
            ),
        ],
    ),
    (
        "mall/wallet.py",
        [
            (
                "    rider_delivery = getattr(order, 'rider_delivery', None)\n",
                "    rider_delivery = order.seller_deliveries.first()\n",
            ),
        ],
    ),
    (
        "mall/handoff.py",
        [
            (
                "            rider = getattr(order, 'rider_delivery', None)\n"
                "            if rider and rider.rider_phone:",
                "            rider = order.seller_deliveries.first()\n"
                "            if rider and rider.rider_phone:",
            ),
            (
                "            rider = getattr(order, 'rider_delivery', None)\n"
                "            if rider:\n"
                "                phone = rider.rider_phone or ''",
                "            rider = order.seller_deliveries.first()\n"
                "            if rider:\n"
                "                phone = rider.rider_phone or ''",
            ),
        ],
    ),
    (
        "mall/admin_views.py",
        [
            (
                "    rider = getattr(order, 'rider_delivery', None)\n",
                "    rider = order.seller_deliveries.first()\n",
            ),
        ],
    ),
    (
        "mall/management/commands/simulate_handoff.py",
        [
            (
                "        if hasattr(order, 'rider_delivery'):\n"
                "            r = order.rider_delivery\n"
                "            self.stdout.write(f'  Rider:       {r.rider_name} ({r.rider_phone})')\n",
                "        r = order.seller_deliveries.first()\n"
                "        if r:\n"
                "            self.stdout.write(f'  Rider:       {r.rider_name} ({r.rider_phone})')\n",
            ),
        ],
    ),
]


def main():
    any_errors = False
    for filepath, replacements in FIXES:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            print(f"[SKIP] {filepath}: file not found")
            any_errors = True
            continue

        changed = False
        for old, new in replacements:
            count = content.count(old)
            if count == 0:
                print(f"[WARN] {filepath}: expected snippet not found "
                      f"(already patched, or context drifted) -- skipping this hunk")
                continue
            if count > 1:
                print(f"[WARN] {filepath}: snippet matched {count} times, "
                      f"expected 1 -- skipping this hunk to avoid a wrong edit")
                any_errors = True
                continue
            content = content.replace(old, new)
            changed = True

        if changed:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[OK]   {filepath}: patched")
        else:
            print(f"[--]   {filepath}: no changes applied")

    if any_errors:
        print("\nSome hunks were skipped -- review the WARN lines above before deploying.")
        sys.exit(1)
    else:
        print("\nAll fixes applied cleanly.")


if __name__ == "__main__":
    main()
