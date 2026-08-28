import os
import time

cfg_path = '.git/config'
if os.path.exists(cfg_path):
    print(open(cfg_path, encoding='utf-8').read()[:600])
else:
    print('NO CONFIG')
print('--- candidate result files (humen/summary/report, recent first):')
hits = []
for r, ds, fs in os.walk('.'):
    ds[:] = [d for d in ds if d not in ('.git', '__pycache__', 'node_modules',
                                        '.venv-rl', '.pytest_cache')]
    for f in fs:
        p = os.path.join(r, f)
        low = f.lower()
        if 'humen' in p.lower() or 'summary' in low or 'report' in low:
            hits.append((os.path.getmtime(p), p))
for m, p in sorted(hits)[-14:]:
    print(time.strftime('%m-%d %H:%M', time.localtime(m)), p)
