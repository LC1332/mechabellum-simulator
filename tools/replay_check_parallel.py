# Parallel wrapper around pysim.replay_check: split a rounds*.json corpus
# into N chunk files (whole replays kept together - replay_check carries
# per-replay state across rounds), run one replay_check process per chunk,
# aggregate the per-chunk --report JSONs into a single summary with exact
# per-round accuracy. Same engine/opts as a plain single-process run.
# usage: python tools/replay_check_parallel.py <rounds.json> [N=8]
#        (summary -> <corpus_dir>/parallel_summary.json)
import json, os, subprocess, sys, concurrent.futures, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(
    ROOT, "local_data", "rounds.json")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 8
CHUNK_DIR = os.path.join(os.path.dirname(SRC), "parallel_chunks")

os.makedirs(CHUNK_DIR, exist_ok=True)
recs = json.load(open(SRC, encoding="utf8"))
chunks = [recs[i::N] for i in range(N)]
paths = []
for i, ch in enumerate(chunks):
    p = os.path.join(CHUNK_DIR, "chunk%02d.json" % i)
    json.dump(ch, open(p, "w", encoding="utf8"), ensure_ascii=False)
    paths.append(p)
print("split %d replays -> %d chunks: %s" % (len(recs), N, CHUNK_DIR), flush=True)


def run_one(i):
    rep = os.path.join(CHUNK_DIR, "report%02d.json" % i)
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "pysim.replay_check",
                        "--rounds", paths[i], "--report", rep],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf8", errors="replace")
    ok = r.returncode == 0
    print("chunk %d: %s (%.0fs)%s" % (i, "ok" if ok else "FAIL",
                                      time.time() - t0,
                                      "" if ok else "\n" + r.stdout[-2000:] + r.stderr[-2000:]),
          flush=True)
    return ok


t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(N) as ex:
    oks = list(ex.map(run_one, range(N)))
print("all done in %.0fs (%d/%d ok)" % (time.time() - t0, sum(oks), N), flush=True)

tot = correct = draws = skipped = 0
round_exact = {}
for i in range(N):
    rep = os.path.join(CHUNK_DIR, "report%02d.json" % i)
    if not os.path.exists(rep):
        continue
    e = json.load(open(rep, encoding="utf8"))[-1]
    tot += e["total"]; correct += e["correct"]
    draws += e["draws"]; skipped += e["skipped"]
    for r, v in (e.get("round_exact") or {}).items():
        a = round_exact.setdefault(int(r), [0, 0])
        a[0] += v["n"]; a[1] += v.get("ok", 0)
out = {"corpus": os.path.basename(SRC), "total": tot, "correct": correct,
       "draws": draws, "skipped": skipped,
       "acc": round(100.0 * correct / tot, 2) if tot else 0,
       "round_exact": {str(r): {"n": v[0], "ok": v[1],
                                "acc": round(100.0 * v[1] / v[0], 1) if v[0] else 0}
                       for r, v in sorted(round_exact.items())}}
summary = os.path.join(os.path.dirname(SRC), "parallel_summary.json")
json.dump(out, open(summary, "w", encoding="utf8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
print("summary ->", summary)
