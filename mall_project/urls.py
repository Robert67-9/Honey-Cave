from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve as _serve_media
from django.contrib.sitemaps.views import sitemap
from django.http import HttpResponse
from mall.sitemaps import StaticViewSitemap, ProductSitemap, CategorySitemap

sitemaps = {
    'static':    StaticViewSitemap,
    'products':  ProductSitemap,
    'categories': CategorySitemap,
}


def robots_txt(request):
    """
    Served at /robots.txt — tells search engine crawlers which paths to skip
    and points them at our sitemap so new products are discovered quickly.
    Uses request.get_host() so it works on any domain without config.
    """
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()
    lines = [
        'User-agent: *',
        'Allow: /',
        # Don't crawl private or session-dependent pages
        'Disallow: /cart/',
        'Disallow: /checkout/',
        'Disallow: /my-orders/',
        'Disallow: /account/',
        'Disallow: /panel/',
        'Disallow: /admin/',
        'Disallow: /api/',
        'Disallow: /login/',
        'Disallow: /register/',
        'Disallow: /verify-otp/',
        'Disallow: /forgot-password/',
        'Disallow: /reset-password/',
        'Disallow: /auth/',
        '',
        f'Sitemap: {scheme}://{host}/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain')


urlpatterns = [
    path('admin/', admin.site.urls),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('', include('mall.urls')),
]

# ─── Serving user-uploaded media (/media/) ────────────────────────────────────
# In DEBUG, Django's static() helper serves media. In production that helper
# is a no-op, which is why uploaded product images 404 when DEBUG=False.
#
# When media is stored on the LOCAL FILESYSTEM (or a mounted persistent disk) —
# i.e. no remote storage backend like Cloudinary/S3 is configured — we serve
# /media/ through Django explicitly so images work in production too. If a
# remote storage backend IS configured, image .url values point at that host
# and this route simply goes unused (harmless).
#
# Note: serving media through Django is fine for a small/medium shop. For very
# high traffic, put a CDN or object store in front. The check below keys off
# the active default storage backend so the route is only added when needed.
_default_storage = (settings.STORAGES.get('default', {}) or {}).get('BACKEND', '')
_using_local_media = 'FileSystemStorage' in _default_storage

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif _using_local_media:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', _serve_media,
                {'document_root': settings.MEDIA_ROOT}),
    ]
