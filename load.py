
import argparse
import time
import urllib.request
import urllib.error

p = argparse.ArgumentParser()
p.add_argument('--seconds', type=int, default=180)
p.add_argument('--rps', type=float, default=10)
a = p.parse_args()
if a.rps <= 0 or a.seconds <= 0:
    p.error('seconds y rps deben ser positivos')
end = time.monotonic() + a.seconds
counts = {200: 0, 500: 0, 'other': 0}
while time.monotonic() < end:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen('http://localhost:8080/api/orders', timeout=5) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 'other'
    counts[code if code in counts else 'other'] += 1
    time.sleep(max(0, 1/a.rps - (time.monotonic() - t0)))
print(counts)
