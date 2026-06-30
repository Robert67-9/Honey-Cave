# Honey Cave Market — Render Deploy Package

## What changed in settings.py

| # | Fix | What was wrong | What's fixed |
|---|-----|----------------|--------------|
| 1 | `DEBUG` default | Was `True` — debug mode if env var missing | Changed to `False` |
| 2 | Media storage | `FileSystemStorage` — files wiped on every deploy | Cloudinary when configured, warns otherwise |
| 3 | Sessions | Pure DB sessions — extra DB query on every request | `cache_db` (Redis primary + DB fallback) when Redis is present |
| 4 | `INSTALLED_APPS` | Missing Cloudinary apps | Added `cloudinary` and `cloudinary_storage` |

---

## Deploy Steps

### 1. Install new dependencies
```bash
pip install cloudinary django-cloudinary-storage
pip freeze > requirements.txt
```

### 2. Set environment variables on Render
Go to **Render Dashboard → Your Service → Environment** and add everything
from `.env.render`. Required fields are:

- `SECRET_KEY`
- `DATABASE_URL` (from Render PostgreSQL dashboard)
- `REDIS_URL` (from Render Redis dashboard — use the **Internal** URL)
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`
- All the security flags (`DEBUG=False`, `SECURE_SSL_REDIRECT=True`, etc.)

If you are using Tiliow for customer notifications, also add:
- `TILIOW_API_KEY`
- `TILIOW_API_URL` (optional; default: `https://api.tiliow.com/v1/messages`)
- `TILIOW_SENDER_ID` (optional)

### 3. Replace your settings.py
Drop `settings.py` into your project, replacing the existing file.

### 4. Run migrations
```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

### 5. Watch for redirect loops
If you get infinite redirects after deploy, set `SECURE_SSL_REDIRECT=False`
in Render's env vars. Render handles HTTPS at the load balancer level.

---

## Cloudinary Setup (5 minutes)
1. Sign up at https://cloudinary.com (free tier = 25 GB)
2. Dashboard → API Keys → copy Cloud Name, API Key, API Secret
3. Add to Render environment variables
4. Existing local media files won't auto-migrate — re-upload or use
   Cloudinary's upload API/dashboard to bulk-import them

---

## Notes
- `.env.render` is a **reference file only** — do not commit it to git
- `REDIS_URL` is set automatically by Render when you attach a Redis instance
- `RENDER_EXTERNAL_HOSTNAME` is set automatically by Render (no action needed)
