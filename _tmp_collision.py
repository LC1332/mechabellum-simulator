# -*- coding: utf-8 -*-
"""T0 pair_id collision check over the full 1106-replay corpus."""
import hashlib, json, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(ROOT, 'local_data', 'humen_rounds.json')
rounds = json.load(open(path, encoding='utf-8'))
corpus_version = hashlib.sha256(open(path, 'rb').read()).hexdigest()[:16]
names = [r['file'] for r in rounds]
dup = {f for f in names if names.count(f) > 1}
ids = set()
n = 0
coll = 0
for r in rounds:
    fname = r['file']
    ident = fname + ('#' + hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12] if fname in dup else '')
    for pidx, pair in enumerate(r['pairs']):
        pid = hashlib.sha1(('%s|%s|%d|%d' % (corpus_version, ident, pair['round'], pidx)).encode()).hexdigest()[:16]
        n += 1
        if pid in ids:
            coll += 1
        ids.add(pid)
print('corpus_version=%s' % corpus_version)
print('replays=%d duplicate_names=%d pairs=%d unique_pair_ids=%d collisions=%d'
      % (len(rounds), len(dup), n, len(ids), coll))
