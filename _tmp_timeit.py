# -*- coding: utf-8 -*-
"""Time one humen chunk battle via pysim.replay_check --limit, to calibrate
the per-battle cost under the step5 engine."""
import subprocess
import sys
import time

t0 = time.time()
r = subprocess.run(
    [sys.executable, "-m", "pysim.replay_check",
     "--rounds", "local_data/humen_chunks/chunk00.json",
     "--limit", "6", "--techs", "full", "--deploy", "fight",
     "--sneak", "round"],
    capture_output=True, text=True, encoding="utf8", errors="replace",
    timeout=900)
print("rc=%s  %.1fs" % (r.returncode, time.time() - t0))
print(r.stdout[-1500:])
print(r.stderr[-600:])
