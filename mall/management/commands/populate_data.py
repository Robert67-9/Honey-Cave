from django.core.management.base import BaseCommand
from mall.models import Category, Product


class Command(BaseCommand):
    help = 'Populate the database with 30+ sample products across 10 categories'

    def handle(self, *args, **kwargs):
        self.stdout.write('\n📦 Loading Market product catalogue...\n')

        # ── Categories ────────────────────────────────────────────────────────
        categories_data = [
            ('Fashion & Clothing',   'fashion'),
            ('Electronics',          'electronics'),
            ('Home & Living',        'home-living'),
            ('Sports & Fitness',     'sports'),
            ('Beauty & Skincare',    'beauty'),
            ('Books & Stationery',   'books'),
            ('Kitchen & Dining',     'kitchen'),
            ('Toys & Games',         'toys'),
            ('Health & Wellness',    'health'),
            ('Bags & Accessories',   'bags'),
        ]
        categories = {}
        for name, slug in categories_data:
            cat, _ = Category.objects.get_or_create(slug=slug, defaults={'name': name})
            categories[slug] = cat
            self.stdout.write(f'  ✓ Category: {name}')

        # ── Products ──────────────────────────────────────────────────────────
        # (name, slug, category_slug, description, price, stock)
        products_data = [
            # ── Fashion & Clothing (5) ────────────────────────────────────────
            ('Classic White Sneakers',
             'classic-white-sneakers', 'fashion',
             'Timeless white leather sneakers with premium stitching and cushioned soles. Goes with every outfit, every season.',
             89.99, 25),
            ('Silk Midi Dress',
             'silk-midi-dress', 'fashion',
             'Elegant A-line silk midi dress in champagne. Perfect for brunches, weddings, and evenings out.',
             149.99, 12),
            ('Men\'s Wool Overcoat',
             'mens-wool-overcoat', 'fashion',
             'Luxurious merino wool overcoat with tailored double-breasted cut. Classic silhouette that never goes out of style.',
             299.99, 8),
            ('Full-Grain Leather Belt',
             'leather-belt', 'fashion',
             'Handcrafted full-grain leather belt with brushed-gold buckle. Width 3.5 cm. Available in brown and black.',
             49.99, 40),
            ('Casual Linen Shirt',
             'casual-linen-shirt', 'fashion',
             'Breathable washed linen shirt in a relaxed fit. Ideal for Ghana\'s warm climate — cool, crisp, and effortless.',
             64.99, 35),

            # ── Electronics (6) ───────────────────────────────────────────────
            ('Wireless Noise-Cancelling Headphones',
             'wireless-headphones', 'electronics',
             '30-hour battery, active noise cancellation, Hi-Res audio support and ultra-padded ear cups for long sessions.',
             249.99, 18),
            ('Smart Watch Pro',
             'smart-watch-pro', 'electronics',
             'Health tracking, GPS, always-on AMOLED display and 5-day battery. Works with iOS and Android.',
             199.99, 22),
            ('Portable Bluetooth Speaker',
             'bluetooth-speaker', 'electronics',
             '360° waterproof speaker, 20-hour battery, IPX7 rating and built-in power bank. Party anywhere.',
             79.99, 35),
            ('USB-C Hub 7-in-1',
             'usb-c-hub', 'electronics',
             'Compact hub with 4K HDMI, 3× USB-A, SD card reader, and 100W PD charging pass-through.',
             59.99, 50),
            ('LED Desk Lamp',
             'led-desk-lamp', 'electronics',
             'Touch-dimming LED lamp with 5 colour temperatures, USB charging port and memory function. Eye-care certified.',
             44.99, 60),
            ('Wireless Charging Pad',
             'wireless-charging-pad', 'electronics',
             '15W fast wireless charger compatible with all Qi devices. Ultra-slim aluminium body with LED indicator.',
             29.99, 75),

            # ── Home & Living (4) ─────────────────────────────────────────────
            ('Scented Soy Candle Set',
             'scented-candle-set', 'home-living',
             'Set of 3 handpoured soy wax candles — Cedarwood, Vanilla Amber, and Citrus Grove. Burns 40+ hours each.',
             39.99, 60),
            ('Washed Linen Throw Blanket',
             'linen-throw-blanket', 'home-living',
             'Oversized pre-washed linen throw, ultra-soft and breathable. Perfect over sofas and beds.',
             69.99, 30),
            ('Ceramic Pour-Over Coffee Set',
             'pour-over-coffee-set', 'home-living',
             'Minimalist matte ceramic dripper, matching carafe and reusable filters. Brews exceptional specialty coffee.',
             54.99, 20),
            ('Bamboo Wall Clock',
             'bamboo-wall-clock', 'home-living',
             'Silent quartz wall clock laser-cut from sustainable bamboo. 30 cm diameter, suits any interior.',
             35.99, 45),

            # ── Sports & Fitness (4) ──────────────────────────────────────────
            ('Premium Yoga Mat',
             'yoga-mat-premium', 'sports',
             '6mm natural rubber yoga mat with alignment lines, non-slip surface and carry strap. Eco-certified.',
             44.99, 45),
            ('Insulated Water Bottle 1L',
             'insulated-water-bottle', 'sports',
             'Double-wall vacuum insulated stainless steel. Keeps drinks cold 24h, hot 12h. BPA-free leak-proof lid.',
             34.99, 80),
            ('Resistance Band Set (5 Levels)',
             'resistance-band-set', 'sports',
             'Five progressive resistance latex bands with anchor attachment, handles, ankle straps and printed exercise guide.',
             24.99, 100),
            ('Jump Rope — Speed Cable',
             'speed-jump-rope', 'sports',
             'Adjustable steel cable jump rope with ball-bearing handles. Suitable for HIIT, boxing and crossfit training.',
             19.99, 90),

            # ── Beauty & Skincare (3) ─────────────────────────────────────────
            ('Vitamin C Brightening Serum',
             'vitamin-c-serum', 'beauty',
             '15% stabilised Vitamin C with hyaluronic acid and ferulic acid. Visibly brightens and evens skin tone in 4 weeks.',
             45.00, 35),
            ('Deep Hydration Face Cream',
             'hydrating-face-cream', 'beauty',
             'Rich ceramide and peptide moisturiser suitable for all skin types. Repairs skin barrier overnight.',
             38.00, 42),
            ('SPF 50 Sunscreen Fluid',
             'spf50-sunscreen', 'beauty',
             'Lightweight, non-greasy broad-spectrum SPF 50 PA++++. Invisible finish — ideal under makeup.',
             32.00, 55),

            # ── Books & Stationery (3) ────────────────────────────────────────
            ('The Art of Design Thinking',
             'art-of-design-thinking', 'books',
             'A beautifully illustrated guide to human-centered design for makers, entrepreneurs and innovators.',
             29.99, 55),
            ('Mindful Living: A Practical Guide',
             'mindful-living-guide', 'books',
             'Research-backed, warm guide to weaving mindfulness into your routines — stress, sleep, and relationships.',
             19.99, 70),
            ('A5 Hardcover Dotted Notebook',
             'hardcover-dotted-notebook', 'books',
             '200gsm ivory dotted pages, lay-flat binding and ribbon bookmark. Beloved by bullet journalists worldwide.',
             14.99, 120),

            # ── Kitchen & Dining (3) ──────────────────────────────────────────
            ('Non-Stick Cast Iron Pan 26cm',
             'cast-iron-pan', 'kitchen',
             'Pre-seasoned cast iron skillet with ergonomic handle. Even heat distribution, oven-safe up to 260°C.',
             79.00, 28),
            ('Bamboo Cutting Board Set',
             'bamboo-cutting-board', 'kitchen',
             'Set of 3 graduated bamboo boards with juice grooves and non-slip feet. Naturally antimicrobial.',
             36.00, 50),
            ('Electric Kettle 1.7L',
             'electric-kettle', 'kitchen',
             'Cordless stainless-steel kettle with 360° base, auto shut-off, boil-dry protection and rapid 2200W element.',
             55.00, 38),

            # ── Toys & Games (3) ─────────────────────────────────────────────
            ('STEM Building Blocks Set (200pcs)',
             'stem-building-blocks', 'toys',
             '200-piece interlocking engineering block set for ages 6+. Develops spatial reasoning and creativity.',
             42.00, 33),
            ('Classic Wooden Chess Set',
             'wooden-chess-set', 'toys',
             'Hand-carved Staunton pieces with weighted bases and a rollable board case. Standard tournament sizing.',
             55.00, 20),
            ('Magnetic Drawing Board',
             'magnetic-drawing-board', 'toys',
             'Mess-free doodle board with multiple coloured magnetic strips and stampers. Perfect for ages 3 and up.',
             18.99, 65),

            # ── Health & Wellness (2) ─────────────────────────────────────────
            ('Digital Body Weight Scale',
             'body-weight-scale', 'health',
             'Tempered glass smart scale accurate to 0.1 kg. Syncs via Bluetooth to iOS/Android health apps.',
             38.00, 45),
            ('Essential Oil Diffuser 500ml',
             'oil-diffuser', 'health',
             'Ultrasonic mist diffuser with 7-colour ambient light, timer and auto shut-off. Runs 10+ hours per fill.',
             42.00, 40),

            # ── Bags & Accessories (3) ────────────────────────────────────────
            ('Leather Tote Bag',
             'leather-tote-bag', 'bags',
             'Structured full-grain leather tote with suede lining, interior zip pocket and gold-tone hardware.',
             185.00, 15),
            ('Canvas Backpack 25L',
             'canvas-backpack', 'bags',
             'Waxed canvas backpack with 15" laptop sleeve, padded straps and antique brass buckles. Built to last.',
             99.00, 22),
            ('Slim Leather Card Wallet',
             'slim-card-wallet', 'bags',
             'Minimalist bifold wallet in full-grain leather. Holds 6 cards, ID window and cash sleeve. RFID-blocking.',
             34.00, 60),
        ]

        self.stdout.write('\n🛍️  Loading products...\n')
        for name, slug, cat_slug, desc, price, stock in products_data:
            prod, created = Product.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': name,
                    'category': categories[cat_slug],
                    'description': desc,
                    'price': price,
                    'stock': stock,
                    'available': True,
                }
            )
            status = '✓ Created' if created else '↩ Already exists'
            self.stdout.write(f'  {status}: {name}')

        total = Product.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'\n🎉 Done! {total} products across {len(categories_data)} categories loaded.\n'
            f'   Run: python manage.py runserver — then visit /products/\n'
        ))
