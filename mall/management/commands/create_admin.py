"""
Creates (or updates the password of) a superuser from environment variables.
Safe to run on every deploy — does nothing if DJANGO_SUPERUSER_USERNAME is
not set, and won't error if the user already exists.

Set these as environment variables on Render:
    DJANGO_SUPERUSER_USERNAME
    DJANGO_SUPERUSER_EMAIL      (optional)
    DJANGO_SUPERUSER_PASSWORD

Usage:
    python manage.py create_admin
"""
import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Creates a superuser from DJANGO_SUPERUSER_* environment variables, if set.'

    def handle(self, *args, **options):
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '')

        if not username or not password:
            self.stdout.write(self.style.WARNING(
                'DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD not set — skipping.'
            ))
            return

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={'email': email, 'is_staff': True, 'is_superuser': True},
        )

        if created:
            user.set_password(password)
            user.email = email
            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f'Superuser "{username}" created.'))
        else:
            # Already exists — update password so you can rotate it by
            # changing the env var and redeploying.
            user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f'Superuser "{username}" already existed — password updated.'))
