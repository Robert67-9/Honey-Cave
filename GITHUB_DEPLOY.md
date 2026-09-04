# Pushing Honey Cave to GitHub (and on to Render)

Your project is small (~8 MB without the virtualenv). The only reason a Django
folder ever feels "too big" is committing `venv/` and `__pycache__/`. The
updated `.gitignore` now excludes those and **keeps `media/`** (your images
must ship since you're not using Cloudinary).

## 1. One-time: make sure the bloat isn't already tracked

If you committed `venv/` before, remove it from tracking (this does NOT delete
the folder on disk):

```bash
git rm -r --cached venv .venv 2>/dev/null
git rm -r --cached "**/__pycache__" 2>/dev/null
```

## 2. Create the repo on GitHub

On github.com → New repository → name it `honeycave` → **don't** add a README
or .gitignore (you already have them) → Create.

## 3. Push from your project folder

```bash
cd path/to/HONEYCAVESSSSs

git init                       # skip if already a git repo
git add .
git commit -m "Honey Cave: production-ready, media + category images, maintenance fixes"
git branch -M main
git remote add origin https://github.com/<your-username>/honeycave.git
git push -u origin main
```

If `git push` rejects because the remote already has commits:
```bash
git pull origin main --allow-unrelated-histories
git push -u origin main
```

## 4. Confirm media actually got pushed

After pushing, open the repo on GitHub and check the `media/products/` and
`media/categories/` folders are there. If they're missing, the old ignore rule
was still cached — run:
```bash
git add media -f
git commit -m "Add media images"
git push
```

## 5. Deploy on Render

Render → New → **Blueprint** → pick your GitHub repo. It reads `render.yaml`
and provisions the web service + Postgres + Redis. Then set the dashboard
secrets noted at the top of `render.yaml` (Paystack keys, SITE_URL, email).

## 6. IMPORTANT — your catalogue data won't transfer automatically

`db.sqlite3` is your **local** database and is gitignored on purpose; Render
uses Postgres, which starts empty. Two ways to get your categories/products
onto the live site:

**Option A — re-enter via the admin panel** (simplest for a small catalogue).

**Option B — export from SQLite, import into Postgres:**
```bash
# locally, against your sqlite db:
python manage.py dumpdata mall --natural-foreign --indent 2 > catalogue.json
```
Commit `catalogue.json`, then in the Render Shell (or a one-off job) run:
```bash
python manage.py loaddata catalogue.json
```
Because image files live in `media/` (which you committed), the image paths in
the data will line up and pictures will display.
