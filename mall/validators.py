from django.core.exceptions import ValidationError


def validate_image_size(value):
    max_mb = 5
    if value.size > max_mb * 1024 * 1024:
        raise ValidationError(f"Image must be under {max_mb}MB (yours is {value.size / 1024 / 1024:.1f}MB).")
