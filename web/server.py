# FastAPI sandbox backend: gamedata + replay import + battle simulation.
# step11: towers (step8 crystals + paralysis) and round buffs (step7-A
# 强化瞄准/高速移动) flow through /api/simulate and /api/replay.
# step9: flank (sneak) teleport delays - u.spawnAt seconds per card; replay
# import restores them via pysim.flank detection (quick teleport = 5s).
# usage:  python web/server.py   (then open http://127.0.0.1:8300)
import sys, os, io, json, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "data")

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pysim.gamedata import GameData
from pysim.engine import battle_from_units
from pysim.deploy import TOWER_POS
from pysim.replay_check import build_tech_map, build_tower_mods
from pysim.flank import (pair_flank_delays, FLANK_DELAY, QT_DELAY, QT_OFFICER)
from pysim.skills import events_from_skill_actions, battle_skill_catalog

GD = GameData(os.path.join(DATA, "gamedata.json"))
# Replay corpus fallback chain: full corpora live in local_data/ (gitignored,
# rounds_new11.json preferred - has towerStrengthen_raw + actions), with a
# small in-repo sample as last resort. Missing every file must not crash the
# server - the sandbox/bench pages still work, only replay import is empty.
ROUNDS = []
ROUNDS_SOURCE = None
for _cand in (os.path.join(ROOT, "local_data", "rounds_new11.json"),
              os.path.join(ROOT, "local_data", "rounds.json"),
              os.path.join(DATA, "samples", "rounds.json")):
    if os.path.exists(_cand):
        ROUNDS = json.load(open(_cand, encoding="utf8"))
        ROUNDS_SOURCE = os.path.relpath(_cand, ROOT)
        break
if ROUNDS_SOURCE is None:
    print("[server] no replay corpus found (local_data/rounds_new11.json, "
          "local_data/rounds.json, data/samples/rounds.json) - "
          "replay import disabled, other pages unaffected")

app = FastAPI(title="Mechabellum Sandbox")


class DeployUnit(BaseModel):
    id: int
    level: int = 1
    x: float
    y: float
    isRotate: bool = False
    techs: list[int] | None = None   # None = card defaults; [] = none
    spawnAt: float = 0.0             # step9 flank teleport seconds


class TowerMods(BaseModel):
    range: float = 0.0    # 强化瞄准: +15 ranged
    speed: float = 0.0    # 高速移动: +3 all


class SimReq(BaseModel):
    p0: list[DeployUnit]
    p1: list[DeployUnit]
    towers: bool = False                 # place the 4 crystals
    towers0: list[int] | None = None     # strengthen levels per tower
    towers1: list[int] | None = None
    mods0: TowerMods | None = None
    mods1: TowerMods | None = None
    skills0: list[dict] | None = None    # step8-B battlefield skill events
    skills1: list[dict] | None = None    # (pysim/skills.py format)


# ---------------- step26 P4: benchmark 播放器 (/bench) ----------------
BENCH_LIBS = {"s24": "step24_scenarios.json", "s25": "step25_scenarios.json",
              "s26": "step26_scenarios.json",
              # step27: 无科技对照/标定/建筑探针 (两塔正中布局)
              "s27": "step27_scenarios.json",
              # step28: "兵种×单科技" 伪兵种基准库 (本轮主战场, 先 250 试水)
              "s28": "step28_scenarios.json",
              # step29: 机制探针+剑齿虎/沙虫NT对照 / 维修剑齿虎标定局 /
              #   爬虫推挤测试矩阵 (review 驱动)
              "s29p": "step29_scenarios.json",
              "s29cal": "step29_cal_scenarios.json",
              "s29c": "step29_crawler_scenarios.json"}
BENCH_SEED = 20220822


def _bench_badges():
    """step28 P2 徽标: 每库 oracle 有效场数 + factory 臂当前一致率 + 版本号
    (版本号 = data/calib/step29/bench_ver.json, 旧版回退 step28/)。
    step29: 版本号字段扩到 s29p/s29cal/s29c。"""
    ver = {}
    vp = os.path.join(DATA, "calib", "step29", "bench_ver.json")
    if not os.path.exists(vp):
        vp = os.path.join(DATA, "calib", "step28", "bench_ver.json")
    if os.path.exists(vp):
        try:
            ver = json.load(open(vp, encoding="utf8"))
        except ValueError:
            ver = {}
    out = {}
    for lib in BENCH_LIBS:
        exp_dir = os.path.join(DATA, "exp", lib)
        if not os.path.isdir(exp_dir):
            continue
        n = agree = 0
        for f in os.listdir(exp_dir):
            if not f.endswith(".json"):
                continue
            try:
                rec = json.load(open(os.path.join(exp_dir, f), encoding="utf8"))
            except ValueError:
                continue
            wo = rec.get("winner_oracle")
            arm = (rec.get("arms") or {}).get("factory") or {}
            if wo is None or arm.get("winner") is None:
                continue
            n += 1
            agree += (wo == arm["winner"])
        out[lib] = {"n": n, "agree": agree,
                    "acc": round(100.0 * agree / n, 1) if n else None,
                    "ver": ver.get(lib) or "factory"}
    return out


@app.get("/api/bench/badges")
def bench_badges():
    return _bench_badges()


def _bench_paths(lib: str):
    exp_dir = os.path.join(DATA, "exp", lib)
    scen_path = os.path.join(DATA, BENCH_LIBS[lib])
    return exp_dir, scen_path


def _tmaps(scenario_side):
    return {int(u["id"]): list((scenario_side.get("techs") or {})
                               .get(str(u["id"]), []))
            for u in scenario_side["units"]}


@app.get("/api/bench/list")
def bench_list(lib: str = "s26"):
    if lib not in BENCH_LIBS:
        raise HTTPException(404, "no such lib")
    exp_dir, scen_path = _bench_paths(lib)
    scen_by_name = {}
    if os.path.exists(scen_path):
        for s in json.load(open(scen_path, encoding="utf8"))["scenarios"]:
            scen_by_name[s["name"]] = s
    out = []
    for f in sorted(os.listdir(exp_dir)):
        if not f.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(exp_dir, f), encoding="utf8"))
        except ValueError:
            continue
        if rec.get("name") is None:
            continue
        s = scen_by_name.get(rec["name"]) or {}
        arms = rec.get("arms") or {}
        out.append({
            "name": rec["name"], "group": rec.get("group"),
            "lineup": rec.get("lineup", s.get("lineup")),
            "desc": rec.get("desc") or s.get("desc") or "",
            "value": rec.get("value") or s.get("value"),
            "techs": {sd: (rec[sd].get("techs") or {}) for sd in ("p0", "p1")},
            "oracle_ok": bool((rec.get("oracle") or {}).get("ok")),
            "wo": rec.get("winner_oracle"),
            "arms": {nm: {"winner": a.get("winner"), "end_t": a.get("end_t")}
                     for nm, a in arms.items()},
        })
    return {"lib": lib, "n": len(out), "scenarios": out}


@app.get("/api/bench/detail")
def bench_detail(lib: str, name: str):
    if lib not in BENCH_LIBS:
        raise HTTPException(404, "no such lib")
    exp_dir, _ = _bench_paths(lib)
    p = os.path.join(exp_dir, name + ".json")
    if not os.path.exists(p):
        raise HTTPException(404, "no such record")
    rec = json.load(open(p, encoding="utf8"))
    # oracle 遥测压缩: 逐 (team, uid) 聚合 dmgReal/kills/存活
    tel = {}
    for un in (rec.get("oracle") or {}).get("units") or []:
        if un.get("rectype") != 0:
            continue
        key = "%d:%d" % (int(un["team"]), int(un["uid"]))
        e = tel.setdefault(key, {"dmgReal": 0.0, "dmgMax": 0.0, "kills": 0})
        e["dmgReal"] += un.get("dmgReal") or 0
        e["dmgMax"] += un.get("dmgMax") or 0
        e["kills"] += un.get("kills") or 0
    rec["oracle_tel"] = tel
    o = rec.get("oracle") or {}
    rec["oracle_summary"] = {"winner": o.get("winner"),
                             "alive": o.get("alive"),
                             "score": o.get("score")}
    return rec


class BenchPlayReq(BaseModel):
    lib: str
    name: str
    opts: dict | None = None          # 引擎变体 (A/B 臂)
    arm: str | None = None            # 记录里的臂名 ("factory"/"mech") 忽略 opts


@app.post("/api/bench/play")
def bench_play(req: BenchPlayReq):
    """现场用引擎 trace=True 重跑该场景, 帧流发给前端逐帧回放 (~2-4s)。"""
    if req.lib not in BENCH_LIBS:
        raise HTTPException(404, "no such lib")
    exp_dir, scen_path = _bench_paths(req.lib)
    s = None
    for sc in json.load(open(scen_path, encoding="utf8"))["scenarios"]:
        if sc["name"] == req.name:
            s = sc
            break
    if s is None:
        # 场景文件可能缺 (旧 exp 内嵌) —— 用 exp 记录里的内嵌场景
        p = os.path.join(exp_dir, req.name + ".json")
        if not os.path.exists(p):
            raise HTTPException(404, "no such scenario")
        s = json.load(open(p, encoding="utf8"))
    opts = {"seed": BENCH_SEED}
    if s.get("group") in ("B", "CAL", "BP"):
        opts["bld_term"] = 2   # step28 定版: 机动单位全灭即终局 (Q-E)
    if req.opts:
        opts.update(req.opts)
    cons = s.get("constructions") or {}
    b = battle_from_units(
        GD, s["p0"]["units"], s["p1"]["units"],
        tech_map0=_tmaps(s["p0"]), tech_map1=_tmaps(s["p1"]),
        opts=opts, trace=True,
        towers0=(s.get("towers") or {}).get("0"),
        towers1=(s.get("towers") or {}).get("1"),
        buildings0=[dict(x=it["x"], y=it["y"], cid=it["cid"], index=k)
                    for k, it in enumerate(cons.get("0") or [])],
        buildings1=[dict(x=it["x"], y=it["y"], cid=it["cid"], index=k)
                    for k, it in enumerate(cons.get("1") or [])])
    t0 = time.perf_counter()
    winner = b.simulate()
    ms = round((time.perf_counter() - t0) * 1000, 1)
    # 帧流: 每 0.1s 采样一帧 (引擎 0.01s/tick), E| 事件行保留
    frames = []
    last_t = -1.0
    for ln in b.trace:
        if ln.startswith("E|"):
            frames.append(ln)
            continue
        t = float(ln.split("|", 1)[0])
        if t - last_t >= 0.099:
            frames.append(ln)
            last_t = t
    return {"lib": req.lib, "name": req.name, "winner": int(winner),
            "end_t": round(b.end_tick * 0.01, 1),
            "alive": [b.alive_count(0), b.alive_count(1)],
            "sim_ms": ms, "opts": opts, "frames": frames}


@app.get("/api/gamedata")
def gamedata():
    mechs = {}
    for mid, m in GD.mechs.items():
        mechs[mid] = {"id": mid, "name": m.name, "life": m.life, "damage": m.damage,
                      "moveSpeed": m.move_speed, "isFly": m.is_fly, "radius": m.radius,
                      "canAttackAir": m.can_attack_air, "canAttackGround": m.can_attack_ground}
    cards = []
    for mid, c in GD.cards.items():
        cards.append({"mechID": c.mech_id, "name": c.name, "mechCount": c.mech_count,
                      "baseMoney": c.base_money, "slotSize": c.slot_size,
                      "cardBaseSize": c.card_base_size, "unlockPrice": c.unlock_price,
                      "group": c.group, "sort": c.sort,
                      "technologies": c.technologies,
                      "defaultTechnologies": c.default_technologies})
    skills = {}
    for sid, s in GD.skills.items():
        skills[sid] = {"type": s.type, "range": s.range, "attackDuration": s.attack_duration,
                       "damage": s.damage[:9] if s.damage else [], "bulletSpeed": s.bullet_speed,
                       "splashRange": s.splash_range}
    techs = {}
    for tid, t in GD.techs.items():
        techs[tid] = {"name": t.name, "description": t.description,
                      "descParams": t.desc_params, "supply": t.supply,
                      "previousTechID": t.previous_tech_id}
    return {"mechs": mechs, "cards": cards, "skills": skills, "techs": techs,
            "towerPos": {t: [list(p) for p in slots] for t, slots in TOWER_POS.items()},
            "battleSkills": battle_skill_catalog()}


@app.get("/api/replays")
def replays():
    out = []
    for i, rec in enumerate(ROUNDS):
        if len(rec.get("players") or []) < 2:   # malformed/synthetic record
            continue
        pairs = []
        for p in rec["pairs"]:
            pairs.append({"round": p["round"],
                          "n0": len(p["p0"]["units"]), "n1": len(p["p1"]["units"]),
                          "label": p["label"]})
        out.append({"idx": i, "file": rec["file"],
                    "p0name": rec["players"][0]["name"], "p1name": rec["players"][1]["name"],
                    "pairs": pairs})
    return out


@app.get("/api/replay/{idx}/{round}")
def replay_round(idx: int, round: int):
    if not (0 <= idx < len(ROUNDS)):
        raise HTTPException(404, "no such replay")
    rec = ROUNDS[idx]
    for p in rec["pairs"]:
        if p["round"] == round:
            rep = (p.get("match") or {}).get("reports") or []
            # step9: per-card teleport seconds ("round" unlock rule won the
            # 226-pair A/B 52.7->54.4 and matches user Q6: re-deploys after
            # a flank death don't re-eat the wait; QT holders get 5s)
            d0, d1 = pair_flank_delays(p, mode="round")
            out = {"file": rec["file"], "round": round, "label": p["label"],
                   "report": rep}
            for k, delays in ((0, d0), (1, d1)):
                pk = p["p%d" % k]
                units = pk.get("units_fight") or pk["units"]
                qt = QT_OFFICER in (pk.get("officers") or [])
                units = [dict(u, spawnAt=float(d)) if d else dict(u)
                         for u, d in zip(units, delays)]
                tm = build_tech_map(GD, pk, "full", {int(u["id"]) for u in units})
                tsl = [int(x) for x in (pk.get("towerStrengthen_raw") or [0, 0])][:2]
                out["p%d" % k] = {"name": rec["players"][k]["name"], "units": units,
                                  "techs": {str(mid): lst for mid, lst in tm.items()},
                                  "towerLevels": tsl,
                                  "mods": build_tower_mods(pk),
                                  "quickTeleport": qt,
                                  "skills": events_from_skill_actions(
                                      pk.get("skill_actions"))}
            return out
    raise HTTPException(404, "no such round")


@app.post("/api/simulate")
def simulate(req: SimReq):
    b = battle_from_units(GD, [u.model_dump() for u in req.p0],
                          [u.model_dump() for u in req.p1], trace=True,
                          tower_mods0=req.mods0.model_dump() if req.mods0 else None,
                          tower_mods1=req.mods1.model_dump() if req.mods1 else None,
                          towers0=req.towers0 if req.towers else None,
                          towers1=req.towers1 if req.towers else None,
                          skills0=req.skills0, skills1=req.skills1)
    if b.alive_count(0) == 0 or b.alive_count(1) == 0:
        raise HTTPException(400, "one side has no deployable units")
    t0 = time.perf_counter()
    winner = b.simulate()
    ms = round((time.perf_counter() - t0) * 1000, 1)
    res = b.result(winner)
    res["sim_ms"] = ms
    return res


@app.get("/bench")
def bench_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "static", "bench.html"))


# ---------------------------------------------------------------- /game
# audit-game assembly (transition前后端审计游戏任务书): manifest-only boot,
# lazy shards, in-memory versioned sessions. A missing corpus is normal.
try:
    from .game_library import GameLibrary
    from .game_service import GameError, GameSessionStore, Economy
    from . import game_api
except ImportError:                       # run as `server:app` (app-dir web)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from game_library import GameLibrary
    from game_service import GameError, GameSessionStore, Economy
    import game_api

GAME_LIBRARY = GameLibrary(ROOT)
GAME_ECO = Economy(GD)
GAME_STORE = GameSessionStore(GAME_LIBRARY, GD, GAME_ECO)
app.include_router(game_api.build_router(GAME_STORE))


@app.exception_handler(GameError)
async def _game_error_handler(request: Request, exc: GameError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.http_status,
                        content={"error": exc.code, "detail": exc.detail})


@app.get("/game")
def game_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "static", "game.html"))


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8300)
