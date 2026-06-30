import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'db.sqlite3')
if not os.path.exists(DB):
    print('db not found:', DB)
    raise SystemExit(1)
conn = sqlite3.connect(DB)
cur = conn.cursor()
try:
    cur.execute("UPDATE mall_sitesettings SET maintenance_mode=0 WHERE id=1")
    conn.commit()
    cur.execute("SELECT maintenance_mode, maintenance_message, maintenance_bypass_token FROM mall_sitesettings WHERE id=1")
    print('row=', cur.fetchone())
except Exception as e:
    print('error:', e)
finally:
    conn.close()
