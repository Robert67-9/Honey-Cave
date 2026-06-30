from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Product, Category


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = 'weekly'

    def items(self):
        return ['home', 'product_list', 'branches', 'contact', 'terms']

    def location(self, item):
        return reverse(item)


class ProductSitemap(Sitemap):
    changefreq = 'daily'
    priority   = 0.9

    def items(self):
        return Product.objects.filter(available=True, stock__gt=0)

    def lastmod(self, obj):
        return obj.updated

    def location(self, obj):
        return reverse('product_detail', args=[obj.slug])


class CategorySitemap(Sitemap):
    changefreq = 'weekly'
    priority   = 0.7

    def items(self):
        return Category.objects.all()

    def location(self, obj):
        return reverse('product_list') + f'?category={obj.slug}'
