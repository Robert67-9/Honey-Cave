import sqlite3
import os
DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'db.sqlite3')
conn = sqlite3.connect(DB)
cur = conn.cursor()
try:
    cur.execute("SELECT maintenance_mode, maintenance_message, maintenance_bypass_token FROM mall_sitesettings WHERE id=1")
    row = cur.fetchone()
    print('row=', row)
except Exception as e:
    print('error:', e)
finally:
    conn.close()
