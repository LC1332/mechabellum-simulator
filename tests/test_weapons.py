# step6b-P3 unit tests: weaponCountPerSkill damage multiplier.
# W1 vulcan w_count=2 when weapons=1 (twin barrels); W2 default off (=1);
# W3 double damage per volley on a lone high-hp target; W4 Group/Standalone
# skills (雷霆 mode1) stay wcp=1; W5 regression: splash tests unaffected.
import io, sys
try:  # only rewrap real console streams (breaks under pytest capture)
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8" \
            and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                      errors="replace")
except Exception:
    pass
import os
from pysim.gamedata import GameData
from pysim.engine import Battle

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
gd = GameData(os.path.join(DATA, "gamedata.json"))
fails = 0


def check(name, got, want):
    global fails
    ok = abs(got - want) < 1e-6 if isinstance(want, float) else got == want
    print("%-38s got %-20s want %-20s %s" % (name, got, want, "OK" if ok else "FAIL"))
    if not ok:
        fails += 1


def setup(mech0, mech1, opts=None, x1=40.0, lv1=1):
    b = Battle(gd)
    if opts:
        b.opts.update(opts)
    b.add_card(0, mech0, 1, -40.0, 0.0)
    b.add_card(1, mech1, lv1, x1, 0.0)
    b.finalize()
    return b


# W1: vulcan (skill 3001, wcp=2) reflects in w_count
b = setup(3, 8, opts={"weapons": 1})
check("W1 vulcan w_count", float(b.w_count[0]), 2.0)
check("W1 steel-ball w_count", float(b.w_count[b.n - 1]), 2.0)

# W2: step15 default -> weapons ON (validated +0.8pp r1-3); opts weapons=0
#     restores the single-instance behavior
b2 = setup(3, 8)
check("W2 default w_count", float(b2.w_count[0]), 2.0)
b2b = setup(3, 8, opts={"weapons": 0})
check("W2b weapons=0 w_count", float(b2b.w_count[0]), 1.0)

# W3: one volley on a lone tanky target deals 2x with weapons on.
#    Use 沙虫-like big target: steel ball id 8 (life 4571, no splash deaths).
def volley_damage(opts):
    b = setup(3, 8, opts=opts)
    hp0 = float(b.hp[b.n - 1])
    for tick in range(300):           # 3s: prep 1s + a few 0.1s volleys
        b.step(tick)
        if b.total_attacks >= 5:
            break
    return (hp0 - float(b.hp[b.n - 1])) / max(1, b.total_attacks)


per3 = volley_damage({"weapons": 1, "init_cd": "none"})
per4 = volley_damage({"weapons": 0, "init_cd": "none"})
check("W3 per-attack dmg ratio (2w/1w)", round(per3 / per4, 3), 2.0)

# W4: 雷霆 (id 27, skill 27001, weaponMode Group=1, wcp=1) stays 1
b5 = setup(27, 8, opts={"weapons": 1})
check("W4 raiden w_count", float(b5.w_count[0]), 1.0)

# W5: known wcp table spot checks (weapons=1)
for mid, want in ((1, 2.0), (11, 2.0), (21, 2.0), (22, 2.0), (9, 1.0)):
    bx = setup(mid, 8, opts={"weapons": 1})
    check("W5 mech %d w_count" % mid, float(bx.w_count[0]), want)

# W6 (step8): projectile buffer compaction - when every in-flight projectile
# lands in the same tick the buffer MUST empty; the old code skipped the
# compaction block, leaving the landed projectile to re-damage its target
# every following tick (user report: 长弓 single shot behaved like a laser).
# Setup: single-module projectile shooter (长弓, techs=[] keeps raw 3.1s
# cycle) vs a tanky single-module rhino; count damage events over 20s.
b6 = Battle(gd)
b6.add_card(0, 2, 1, 0.0, 0.0, techs=[])
b6.add_card(1, 5, 1, 0.0, -120.0, techs=[])
b6.finalize()
events6 = []
_orig_ad = Battle._apply_damage
def _counting_ad(self, tick):
    for vv, dd in zip(getattr(self, "_ev_victim", []) or [],
                      getattr(self, "_ev_dmg", []) or []):
        if self.mech_id[vv] == 5:
            events6.append((round(self.time, 2), float(dd)))
    return _orig_ad(self, tick, )
Battle._apply_damage = _counting_ad
for tick in range(2000):
    b6.step(tick)
Battle._apply_damage = _orig_ad
per_shot = [d for _, d in events6]
gaps = [round(events6[i + 1][0] - events6[i][0], 2) for i in range(len(events6) - 1)]
check("W6 every event = one full shot dmg", all(abs(d - per_shot[0]) < 1e-6 for d in per_shot), True)
check("W6 shot cadence >= 2s (no per-tick re-damage)", (min(gaps) if gaps else 9) >= 2.0, True)
check("W6 event count over 20s (<=7 shots)", len(events6) <= 7, True)

print()
print("RESULT: %d checks failed" % fails)
print("ALL PASS" if fails == 0 else "FAILURES")


def test_all():
    assert not fails, "test_weapons.py: %s checks failed" % len(fails)
