"""
Management command to auto-match images in media/products/ to existing
Product rows by filename.

Place this file at:
    mall/management/commands/match_product_images.py

Then run:
    python manage.py match_product_images --dry-run
    python manage.py match_product_images               # apply changes
    python manage.py match_product_images --overwrite   # also replace existing images

Matching strategy (in order):
    1. Exact match: filename stem (underscores -> spaces) == product.name
    2. Slug match: slugify(filename stem) == product.slug
    3. Loose match: normalized filename in normalized product name
"""
import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from mall.models import Product


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

# Strip Django's auto-suffix (e.g. _LIxAbhV) added on re-upload collisions
DJANGO_SUFFIX_RE = re.compile(r'_[A-Za-z0-9]{7}$')


def normalize(text: str) -> str:
    """Lowercase, strip non-alphanumerics — for fuzzy comparison."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def filename_to_name(filename: str) -> str:
    """Convert 'Softcare_Diaper_L11_Blue.jpg' -> 'Softcare Diaper L11 Blue'."""
    stem = Path(filename).stem
    stem = DJANGO_SUFFIX_RE.sub('', stem)  # strip _LIxAbhV style suffix
    return stem.replace('_', ' ').strip()


class Command(BaseCommand):
    help = "Auto-match images in media/products/ to Products by filename."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Show what would change without saving.",
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help="Replace images on products that already have one.",
        )
        parser.add_argument(
            '--folder',
            default='products',
            help="Subfolder under MEDIA_ROOT to scan (default: products).",
        )

    def handle(self, *args, **opts):
        dry_run = opts['dry_run']
        overwrite = opts['overwrite']
        folder = opts['folder']

        media_root = Path(settings.MEDIA_ROOT)
        scan_dir = media_root / folder
        if not scan_dir.exists():
            self.stderr.write(self.style.ERROR(f"Folder not found: {scan_dir}"))
            return

        # Build product lookup tables once
        products = list(Product.objects.all())
        by_name = {p.name.lower(): p for p in products}
        by_slug = {p.slug.lower(): p for p in products}
        by_norm_name = {normalize(p.name): p for p in products}

        self.stdout.write(self.style.NOTICE(
            f"Scanning {scan_dir} against {len(products)} products"
            + (" (DRY RUN)" if dry_run else "")
        ))

        matched = []      # (filename, product, strategy)
        skipped_has_image = []
        unmatched = []

        for entry in sorted(scan_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in IMAGE_EXTS:
                continue

            cleaned_name = filename_to_name(entry.name)

            # 1. Exact name match
            product = by_name.get(cleaned_name.lower())
            strategy = 'exact'

            # 2. Slug match
            if not product:
                product = by_slug.get(slugify(cleaned_name))
                strategy = 'slug'

            # 3. Loose normalized match
            if not product:
                product = by_norm_name.get(normalize(cleaned_name))
                strategy = 'normalized'

            if not product:
                unmatched.append(entry.name)
                continue

            if product.image and not overwrite:
                skipped_has_image.append((entry.name, product))
                continue

            matched.append((entry, product, strategy))

        # Apply changes
        for entry, product, strategy in matched:
            relative_path = f"{folder}/{entry.name}"
            self.stdout.write(
                f"  [{strategy:10s}] {entry.name}  ->  {product.name} (id={product.pk})"
            )
            if not dry_run:
                product.image.name = relative_path
                product.save(update_fields=['image'])

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Matched:           {len(matched)}"))
        self.stdout.write(f"Skipped (had image): {len(skipped_has_image)}")
        self.stdout.write(self.style.WARNING(f"Unmatched files:    {len(unmatched)}"))

        if skipped_has_image:
            self.stdout.write("")
            self.stdout.write("Skipped products (already have an image):")
            for fn, p in skipped_has_image[:10]:
                self.stdout.write(f"  - {p.name} (file would have been: {fn})")
            if len(skipped_has_image) > 10:
                self.stdout.write(f"  ... and {len(skipped_has_image) - 10} more")
            self.stdout.write("  Re-run with --overwrite to replace these.")

        if unmatched:
            self.stdout.write("")
            self.stdout.write("Unmatched filenames (no product found):")
            for fn in unmatched:
                self.stdout.write(f"  - {fn}")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.NOTICE(
                "DRY RUN — no changes saved. Re-run without --dry-run to apply."
            ))
