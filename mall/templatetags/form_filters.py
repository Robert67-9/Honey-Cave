"""
Generic template filters used across forms and dict-like objects.

Usage in any template:
    {% load form_filters %}
    {{ my_dict|get_item:'some_key' }}
    {{ form|get_item:field_name }}

Why these exist:
  Django templates can't do `dict[key]` when the key is itself a variable.
  These helpers fill that gap without being opinionated about what's being
  looked up (works on dicts, form objects, lists with int keys, etc.).
"""
from django import template

register = template.Library()


@register.filter(name='get_item')
def get_item(obj, key):
    """
    Look up `key` on `obj`. Tries dict access first, then attribute access,
    then list/tuple indexing. Returns '' on miss instead of raising — keeps
    templates from crashing on missing keys.
    """
    if obj is None:
        return ''
    # Dict-like
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        pass
    # Form / object attribute
    try:
        return getattr(obj, key)
    except (AttributeError, TypeError):
        pass
    # Integer index into a list/tuple if key looks numeric
    try:
        return obj[int(key)]
    except (KeyError, TypeError, IndexError, ValueError):
        pass
    return ''
