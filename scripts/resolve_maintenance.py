import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'db.sqlite3'
print('DB path:', DB_PATH)
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="mall_sitesettings"')
if not cur.fetchone():
    raise SystemExit('mall_sitesettings table not found')
cur.execute('SELECT id, maintenance_mode, maintenance_message, maintenance_bypass_token FROM mall_sitesettings WHERE id=1')
row = cur.fetchone()
print('before:', row)
if row is None:
    cur.execute('INSERT INTO mall_sitesettings (id, maintenance_mode, maintenance_message, maintenance_bypass_token, updated) VALUES (1, 0, "Maintenance disabled by deploy script", "", CURRENT_TIMESTAMP)')
else:
    cur.execute('UPDATE mall_sitesettings SET maintenance_mode=0 WHERE id=1')
conn.commit()
cur.execute('SELECT id, maintenance_mode, maintenance_message, maintenance_bypass_token FROM mall_sitesettings WHERE id=1')
row2 = cur.fetchone()
print('after:', row2)
conn.close()
