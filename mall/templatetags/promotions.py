"""
Template tags for rendering internal promotional banners.

Usage in any template:
    {% load promotions %}
    {% promotions_for 'home_hero' %}

The tag:
  1. Pulls all live promotions for that placement, highest priority first
  2. Increments the impression count in a single bulk UPDATE (cheap)
  3. Renders the 'mall/includes/promotion_block.html' partial with the list
"""
from django import template
from django.db import OperationalError, ProgrammingError
from django.db.models import F
from ..models import Promotion

register = template.Library()


@register.inclusion_tag('mall/includes/promotion_block.html', takes_context=True)
def promotions_for(context, placement):
    try:
        promos = list(Promotion.active_for(placement))
        if promos:
            ids = [p.pk for p in promos]
            # Bump impressions atomically. One UPDATE regardless of how many promos.
            Promotion.objects.filter(pk__in=ids).update(impressions=F('impressions') + 1)
    except (OperationalError, ProgrammingError):
        # Table may not exist yet (first deploy before migrate runs, or SQLite reset).
        # Fail silently so the rest of the page still loads.
        promos = []
    return {
        'promotions': promos,
        'placement':  placement,
        'request':    context.get('request'),
    }
