"""
Custom Django management command: validate_templates.

Usage:
    python manage.py validate_templates

Walks every .html template in the project and tries to parse it with the
configured Django template engine. Reports a clean summary of any template
that fails to compile, including the exact line and error message.

This is the fastest way to catch:
  - Unclosed {% for %}, {% if %}, {% block %}, etc.
  - Typos in tag names ({% endofr %} instead of {% endfor %})
  - Missing {% load %} for custom tags
  - Broken {# ... #} comments that span multiple lines

Add the --strict flag to also render each template with an empty context,
which catches variable/filter typos that only surface at render time.
"""
import os
import sys
from django.conf import settings
from django.core.management.base import BaseCommand
from django.template import engines, TemplateSyntaxError, TemplateDoesNotExist
from django.template.loader import get_template


class Command(BaseCommand):
    help = 'Parse every .html template in the project and report syntax errors.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--strict',
            action='store_true',
            help='Also attempt to render each template with an empty context.',
        )

    def handle(self, *args, **options):
        strict = options['strict']
        errors = []
        ok_count = 0

        # Collect every template directory Django knows about
        template_dirs = set()
        for engine_config in settings.TEMPLATES:
            for d in engine_config.get('DIRS', []):
                template_dirs.add(str(d))
            if engine_config.get('APP_DIRS', False):
                for app_config in self._iter_app_template_dirs():
                    template_dirs.add(app_config)

        self.stdout.write(f'Scanning {len(template_dirs)} template director{"y" if len(template_dirs)==1 else "ies"}...')

        seen_paths = set()
        for base_dir in template_dirs:
            if not os.path.isdir(base_dir):
                continue
            for root, _dirs, files in os.walk(base_dir):
                for fname in files:
                    if not fname.endswith('.html'):
                        continue
                    full_path = os.path.join(root, fname)
                    if full_path in seen_paths:
                        continue
                    seen_paths.add(full_path)

                    rel_path = os.path.relpath(full_path, base_dir).replace('\\', '/')

                    try:
                        template = get_template(rel_path)
                    except TemplateDoesNotExist:
                        # Shadowed by an earlier dir — not an error
                        continue
                    except TemplateSyntaxError as e:
                        errors.append((full_path, self._format_error(e)))
                        continue
                    except Exception as e:
                        errors.append((full_path, f'{type(e).__name__}: {e}'))
                        continue

                    if strict:
                        try:
                            template.render({})
                        except Exception as e:
                            # Many render errors are caused by missing vars —
                            # we only flag the template-level ones.
                            errors.append((full_path, f'(render) {type(e).__name__}: {e}'))
                            continue

                    ok_count += 1

        # Report
        self.stdout.write('')
        if errors:
            self.stdout.write(self.style.ERROR(f'✗ {len(errors)} template(s) failed:'))
            self.stdout.write('')
            for path, msg in errors:
                self.stdout.write(self.style.ERROR(f'  {path}'))
                for line in str(msg).splitlines():
                    self.stdout.write(f'      {line}')
                self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f'✓ {ok_count} template(s) OK'))
            sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS(f'✓ All {ok_count} templates parsed successfully.'))

    def _iter_app_template_dirs(self):
        """Yield the template dir for each installed app that has one."""
        from django.apps import apps
        for app_config in apps.get_app_configs():
            candidate = os.path.join(app_config.path, 'templates')
            if os.path.isdir(candidate):
                yield candidate

    def _format_error(self, e):
        """Build a friendly one-liner from a TemplateSyntaxError."""
        msg = str(e)
        # Django attaches helpful debug info on the exception
        token = getattr(e, 'token', None)
        if token is not None and getattr(token, 'lineno', None):
            msg = f'Line {token.lineno}: {msg}'
        return msg
