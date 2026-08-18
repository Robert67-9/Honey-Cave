release: python manage.py migrate --noinput
web: gunicorn mall_project.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 60
