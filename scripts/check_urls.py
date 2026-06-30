import urllib.request
import urllib.error

BASE = 'http://127.0.0.1:8000'
paths = ['/', '/products/', '/cart/', '/login/', '/panel/']
for p in paths:
    url = BASE + p
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        print(p, resp.getcode())
    except urllib.error.HTTPError as e:
        print(p, 'HTTPError', e.code)
    except Exception as e:
        print(p, 'Error', e)
