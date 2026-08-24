# -*- coding: utf-8 -*-
"""Benchmark runner: pysim 重算 vs oracle 真值对拍 (单腿, 无需 Windows 注入)。

每个基准库 (data/step2X_scenarios.json) 的每场对局用当前引擎 (默认出厂
opts, 固定种子) 重算胜负, 与 data/exp/<lib>/ 里活体游戏 oracle 跑出的
winner_oracle 对比, 输出一致率。历史定版数字见 data/calib/step29/bench_ver.json。

用法 (在仓库根执行):
  python benchmarks/run.py --lib s24        # 单库
  python benchmarks/run.py --lib all       # 全部 8 库
  python benchmarks/run.py --lib s24 --only u001,u002
  python benchmarks/run.py --lib s24 --opt kite_dist=40   # 自定义 opts 臂
  python benchmarks/run.py --lib all --regen-exp
      # 重算的完整记录 (含刷新的 factory 臂) 写到 local_data/exp_regen/<lib>/,
      # data/exp/ 真值不受影响
"""
import sys, io, os, json, time, argparse, collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8" \
            and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                      errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

BENCH_LIBS = {"s24": "step24_scenarios.json", "s25": "step25_scenarios.json",
              "s26": "step26_scenarios.json",
              # step27: 无科技对照/标定/建筑探针 (两塔正中布局)
              "s27": "step27_scenarios.json",
              # step28: "兵种×单科技" 伪兵种基准库
              "s28": "step28_scenarios.json",
              # step29: 机制探针+剑齿虎/沙虫NT对照 / 维修剑齿虎标定局 /
              #   爬虫推挤测试矩阵 (review 驱动)
              "s29p": "step29_scenarios.json",
              "s29cal": "step29_cal_scenarios.json",
              "s29c": "step29_crawler_scenarios.json"}
SEED = 20220822          # 与场景生成/oracle 管线一致的定版种子
LIB_ORDER = ["s24", "s25", "s26", "s27", "s28", "s29p", "s29cal", "s29c"]


def _tmaps(s):
    t0 = {int(u["id"]): list((s["p0"].get("techs") or {}).get(str(u["id"]), []))
          for u in s["p0"]["units"]}
    t1 = {int(u["id"]): list((s["p1"].get("techs") or {}).get(str(u["id"]), []))
          for u in s["p1"]["units"]}
    return t0, t1


def _opts_for(s, arm_opts):
    # 纯建筑/标定局: step28 定版 bld_term=2 (机动单位全灭即终局)
    o = dict({"seed": SEED, "bld_term": 2} if s.get("group") in ("B", "CAL", "BP")
             else {"seed": SEED})
    o.update(arm_opts)
    return o


def run_side(gd, s, arm_opts):
    """重算一场, 返回 refresh 记录格式的臂结果 (与 exp 记录 arms.* 同构)。"""
    from pysim.engine import battle_from_units
    tm0, tm1 = _tmaps(s)
    cons = s.get("constructions") or {}
    b = battle_from_units(
        gd, s["p0"]["units"], s["p1"]["units"],
        tech_map0=tm0, tech_map1=tm1, opts=_opts_for(s, arm_opts),
        towers0=(s.get("towers") or {}).get("0"),
        towers1=(s.get("towers") or {}).get("1"),
        buildings0=[dict(x=it["x"], y=it["y"], cid=it["cid"], index=k)
                    for k, it in enumerate(cons.get("0") or [])],
        buildings1=[dict(x=it["x"], y=it["y"], cid=it["cid"], index=k)
                    for k, it in enumerate(cons.get("1") or [])])
    w = b.simulate()
    return {"winner": int(w), "alive": [b.alive_count(0), b.alive_count(1)],
            "towers_down": {str(k): v for k, v in dict(b.towers_down).items()},
            "bld_groups": {str(k): v for k, v in b.building_groups().items()},
            "end_t": round(b.end_tick * 0.01, 1)}


def load_scen_by_name(lib):
    p = os.path.join(DATA, BENCH_LIBS[lib])
    if not os.path.exists(p):
        return {}
    return {s["name"]: s for s in json.load(open(p, encoding="utf8"))["scenarios"]}


def parse_opt_kv(spec):
    opts = {}
    for kv in (spec.split(",") if spec else []):
        if "=" not in kv:
            raise SystemExit("bad --opt entry: %r (want k=v)" % kv)
        k, v = kv.split("=", 1)
        try:
            fv = float(v)
            opts[k] = int(fv) if fv == int(fv) else fv
        except ValueError:
            opts[k] = v
    return opts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", required=True,
                    help="库名 (%s) 或 all" % "|".join(LIB_ORDER))
    ap.add_argument("--only", default="",
                    help="逗号分隔的场景名过滤")
    ap.add_argument("--opt", default="",
                    help="opts 覆写, 如 kite_dist=40,wraith_guns=1")
    ap.add_argument("--arm", default="factory",
                    help="臂名 (写进 --regen-exp 记录, 默认 factory)")
    ap.add_argument("--regen-exp", action="store_true",
                    help="重算记录写到 local_data/exp_regen/<lib>/ (不动 data/exp)")
    a = ap.parse_args()

    libs = LIB_ORDER if a.lib == "all" else [a.lib]
    for lib in libs:
        if lib not in BENCH_LIBS:
            raise SystemExit("unknown lib %r (want %s or all)"
                             % (lib, "|".join(LIB_ORDER)))

    from pysim.gamedata import GameData
    gd = GameData(os.path.join(DATA, "gamedata.json"))
    arm_opts = parse_opt_kv(a.opt)
    only = set(x for x in a.only.split(",") if x)

    ver = {}
    vp = os.path.join(DATA, "calib", "step29", "bench_ver.json")
    if os.path.exists(vp):
        ver = json.load(open(vp, encoding="utf8"))

    tot_n = tot_agree = 0
    t_all = time.time()
    for lib in libs:
        exp_dir = os.path.join(DATA, "exp", lib)
        if not os.path.isdir(exp_dir):
            print("[skip] %s: no data/exp/%s" % (lib, lib))
            continue
        by = load_scen_by_name(lib)
        files = sorted(f for f in os.listdir(exp_dir) if f.endswith(".json"))
        if only:
            files = [f for f in files if f[:-5] in only]
        n = agree = ok = 0
        t0 = time.time()
        regen_dir = (os.path.join(ROOT, "local_data", "exp_regen", lib)
                     if a.regen_exp else None)
        for f in files:
            rec = json.load(open(os.path.join(exp_dir, f), encoding="utf8"))
            s = by.get(rec["name"]) or rec   # 旧 exp 记录内嵌场景
            res = run_side(gd, s, arm_opts)
            n += 1
            wo = rec.get("winner_oracle")
            if wo is not None:
                ok += 1
                agree += (wo == res["winner"])
            if regen_dir:
                rec.setdefault("arms", {})[a.arm] = res
                os.makedirs(regen_dir, exist_ok=True)
                json.dump(rec, open(os.path.join(regen_dir, f), "w",
                                    encoding="utf8"),
                          ensure_ascii=False, indent=1)
            if n % 100 == 0:
                print("  %s %d/%d (%.1f%%, %.0fs)" % (
                    lib, n, len(files), 100.0 * agree / max(1, ok),
                    time.time() - t0), flush=True)
        tot_n += ok
        tot_agree += agree
        ref = ver.get(lib)
        pct = 100.0 * agree / max(1, ok)
        mark = ""
        if ref:
            ra, rn = (int(x) for x in ref.split("/"))
            mark = "" if (agree, ok) == (ra, rn) else \
                "  (!! bench_ver=%s)" % ref
        print("%-6s %4d/%4d = %5.1f%%  (%.0fs)%s" % (
            lib, agree, ok, pct, time.time() - t0, mark))
    if len(libs) > 1:
        print("TOTAL  %4d/%4d = %5.1f%%  (%.0fs)" % (
            tot_agree, tot_n, 100.0 * tot_agree / max(1, tot_n),
            time.time() - t_all))


if __name__ == "__main__":
    main()
