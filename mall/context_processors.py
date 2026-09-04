def cart_count(request):
    cart = request.session.get('cart', {})
    try:
        count = sum(int(v) for v in cart.values())
    except (TypeError, ValueError):
        count = 0
    return {'cart_count': count}


# ─── Branding ─────────────────────────────────────────────────────────────────
# Change these values once here and they update across the entire site:
# navbar, footer, social share previews (Open Graph / Twitter), and the
# JSON-LD structured data used by search engines.
#
# To change your logo image: replace the file at
#     staticfiles/images/logo.png   (square, 512×512 transparent PNG recommended)
# To change the social share preview banner: replace
#     staticfiles/images/og-image.jpg   (1200×630, JPG or PNG)
# To change the favicon: replace
#     staticfiles/images/favicon.ico
# No template edits needed — just drop new files with the same names.

SITE_BRAND        = 'Honey Cave Market'
SITE_BRAND_SHORT  = 'HCM'
SITE_SLOGAN       = 'Premium Shopping, Delivered Across Ghana'
SITE_LOGO_PATH    = '/static/images/logo.png'        # square logo for schema + navbar
SITE_OG_IMAGE     = '/static/images/og-image.jpg'     # 1200x630 social preview banner


def branding(request):
    """
    Expose brand identity (name, slogan, logo path) and admin-editable
    site settings (contact info, social links) to every template.
    Templates can reference {{ site_brand }}, {{ site_slogan }},
    {{ site_settings.phone_primary }}, {{ site_settings.email }}, etc.
    """
    # Lazy import — avoids circular imports at module load time.
    from .models import SiteSettings
    from django.conf import settings as _dj
    try:
        settings_obj = SiteSettings.load()
    except Exception:
        # During migrations the table may not exist yet. Fall back silently.
        settings_obj = None
    google_login_enabled = bool(
        (getattr(_dj, 'GOOGLE_CLIENT_ID', '') or '').strip() and
        (getattr(_dj, 'GOOGLE_CLIENT_SECRET', '') or '').strip()
    )

    # Prefer admin-uploaded logo from SiteSettings; fall back to the static
    # file at /static/images/logo.png if no upload is present.
    logo_path = SITE_LOGO_PATH
    og_image  = SITE_OG_IMAGE
    if settings_obj:
        if settings_obj.logo and hasattr(settings_obj.logo, 'url'):
            logo_path = settings_obj.logo.url
        if settings_obj.og_image and hasattr(settings_obj.og_image, 'url'):
            og_image = settings_obj.og_image.url

    return {
        'site_brand':       SITE_BRAND,
        'site_brand_short': SITE_BRAND_SHORT,
        'site_slogan':      SITE_SLOGAN,
        'site_logo_path':   logo_path,
        'site_og_image':    og_image,
        'site_settings':    settings_obj,
        'google_login_enabled': google_login_enabled,
    }
