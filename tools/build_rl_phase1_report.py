#!/usr/bin/env python
"""R5: assemble the Phase 1 static report (report.md + report.html) from
the run artifacts: data_report, baseline_report, value/policy seed reports,
arena_report + matches, run manifest. Numbers-first, gates explicit."""
import argparse
import gzip
import json
import os
import subprocess
import sys
import time
from collections import Counter

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    p = os.path.join(ROOT, path) if not os.path.isabs(path) else path
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    rd = args.run_dir
    data = load(os.path.join(rd, "data_report.json")) or {}
    base = load(os.path.join(rd, "baseline_report.json")) or {}
    arena = load(os.path.join(rd, "arena_report.json")) or {}
    v_reports = []
    for s in (0, 1, 2):
        r = load(os.path.join(rd, "value_report_seed%d.json" % s))
        if r:
            v_reports.append((s, r))
    p_reports = []
    for s in (0, 1, 2):
        r = load(os.path.join(rd, "policy_report_seed%d.json" % s))
        if r:
            p_reports.append((s, r))

    contract = load("data/rl_phase1_contract.json") or {}
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True,
                                cwd=ROOT).stdout.strip()
    except OSError:
        commit = "?"

    L = []
    A = L.append
    A("# RL Phase 1 运行报告(%s)" % os.path.basename(rd.rstrip("/")))
    A("")
    A("- 代码 commit: `%s`,contract: `%s` / `%s` / `%s`" % (
        commit, contract.get("contract_version"),
        contract.get("schema_version"), contract.get("engine_version")))
    A("- 生成时间:%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    A("")

    # ---------------- data
    A("## 1. 数据(T1)")
    A("")
    rows = data.get("rows") or {}
    A("| 数据集 | 行数 |")
    A("|---|---|")
    for k, v in rows.items():
        A("| %s | %s |" % (k, v))
    A("")
    A("- 游戏/去重组:%s 局 → %s 个 duplicate group;split:%s" % (
        data.get("n_games"), data.get("n_duplicate_groups"),
        json.dumps(data.get("splits"), default=str)))
    if base:
        A("- 健康:T1 target-in-verbmask recall = **%s**(n=%s);split 泄漏 = %s;"
          "observation 泄漏 = %s" % (
              base.get("health", {}).get("gold_target_in_verbmask_recall"),
              base.get("health", {}).get("gold_target_in_verbmask_n"),
              len(base.get("health", {}).get("split_leaks_battle", [])),
              len(base.get("health", {}).get("observation_leakage", []))))
        A("- label-shuffle:test NLL %.4f → 打乱 %.4f(应上升)" % (
            base.get("health", {}).get("test_nll_clean", float("nan")),
            base.get("health", {}).get("label_shuffle_test_nll",
                                       float("nan"))))
    A("")

    # ---------------- baselines
    if base:
        A("## 2. 非神经 baseline")
        A("")
        A("| 模型 | val NLL | val acc | test NLL |")
        A("|---|---|---|---|")
        for name, e in base.get("value", {}).items():
            A("| %s | %.4f | %.3f | %.4f |" % (
                name, e["validation"]["wdl"]["nll"],
                e["validation"]["wdl"]["acc"],
                e["test"]["wdl"]["nll"]))
        A("")
        A("policy verb top1:")
        A("")
        A("| baseline | validation | test |")
        A("|---|---|---|")
        for name, e in base.get("policy", {}).items():
            A("| %s | %.4f | %.4f |" % (name, e["validation"]["verb_top1"],
                                        e["test"]["verb_top1"]))
        A("")

    # ---------------- value
    if v_reports:
        A("## 3. V_battle_sim / V_battle_real(T2)")
        A("")
        A("| seed | domain | split | NLL | acc | dmg MAE | side-swap maxΔ |")
        A("|---|---|---|---|---|---|---|")
        for s, r in v_reports:
            for dom, entry in r.get("domains", {}).items():
                for split, m in entry.items():
                    A("| %d | %s | %s | %.4f | %.3f | %.4f | %.4f |" % (
                        s, dom, split, m["nll"], m["acc"],
                        m["damage"]["mae"], m["side_swap_wdl_max_diff"]))
        accs = [r[1].get("sim_ranking_pairwise_acc") for r in v_reports]
        accs = [a for a in accs if a is not None]
        if accs:
            A("")
            A("- sim 候选排序 pairwise acc:%s(gate ≥ 0.65)→ **%s**" % (
                ["%.3f" % a for a in accs],
                "PASS" if min(accs) >= 0.65 else "FAIL"))
        prior = 0.6931
        tnlls = [r[1]["domains"]["real"]["test"]["nll"] for r in v_reports
                 if "real" in r[1].get("domains", {})]
        if tnlls:
            import statistics as _st
            med = _st.median(tnlls)
            A("- V_real test NLL %s(中位 %.4f)vs constant prior %.4f"
              "→ **%s**" % (
                  ["%.4f" % x for x in tnlls], med, prior,
                  "PASS" if med < prior else "FAIL"))
        A("")

    # ---------------- policy
    if p_reports:
        A("## 4. pi_BC(T3,teacher-forced)")
        A("")
        A("| seed | split | verb top1 | verb top3 | unit ptr top1 | ptr top1 | END P | END R | 非法概率质量 |")
        A("|---|---|---|---|---|---|---|---|---|")
        for s, r in p_reports:
            for split, m in r.get("train_metrics", {}).items():
                A("| %d | %s | %.4f | %.4f | %.4f | %.4f | %.4f | %.4f | %.5f |" % (
                    s, split, m["verb_top1"], m["verb_top3"],
                    m["unit_ptr_top1"], m["ptr_top1"], m["end_precision"],
                    m["end_recall"], m["illegal_prob_mass"]))
        A("")

    # ---------------- arena
    if arena:
        A("## 5. Arena / best-of-N(T4)")
        A("")
        A("| matchup | n | mean ego reward | win | loss |")
        A("|---|---|---|---|---|")
        for key, e in arena.get("matchups", {}).items():
            if key == "best_of_n" or not isinstance(e, dict):
                continue
            A("| %s | %d | %.4f | %.2f | %.2f |" % (
                key, e["n"], e["mean"], e["win_share"], e["loss_share"]))
        bon = arena.get("matchups", {}).get("best_of_n")
        if bon:
            A("")
            A("**best-of-N paired improvement**: mean gain %.4f,95%% CI %s,"
              "win rate %.2f(gate:CI 下界 > 0)→ **%s**" % (
                  bon["mean_gain"],
                  ["%.4f" % x for x in bon.get("ci95", [float("nan")] * 2)],
                  bon.get("win_rate", float("nan")),
                  "PASS" if bon.get("ci95", [1])[0] > 0 else "FAIL"))
            if bon.get("value_topk_recall") is not None:
                A("- V_sim prefilter top-k recall: %.3f(gate ≥ 0.90)" %
                  bon["value_topk_recall"])
        A("- 总 rejection:%s(gate = 0)→ **%s**" % (
            arena.get("matchups", {}).get("total_rejections"),
            "PASS" if arena.get("matchups", {}).get(
                "total_rejections") == 0 else "FAIL"))
        A("")

    # ---------------- gates summary
    A("## 6. Gate 汇总")
    A("")
    A("| gate | 结论 |")
    A("|---|---|")
    gates = []
    if base:
        rec = base.get("health", {}).get("gold_target_in_verbmask_recall")
        gates.append(("T1 target-in-mask recall 100%",
                      "PASS" if rec == 1.0 else "FAIL(%s)" % rec))
        gates.append(("T1 无 split/observation 泄漏",
                      "PASS" if not base.get("health", {}).get(
                          "split_leaks_battle") and not
                      base.get("health", {}).get("observation_leakage")
                      else "FAIL"))
    if v_reports:
        accs = [r[1].get("sim_ranking_pairwise_acc") for r in v_reports
                if r[1].get("sim_ranking_pairwise_acc")]
        gates.append(("T2 sim ranking ≥ 0.65",
                      "PASS" if accs and min(accs) >= 0.65 else "FAIL"))
        tnlls = [r[1]["domains"]["real"]["test"]["nll"] for r in v_reports
                 if "real" in r[1].get("domains", {})]
        if tnlls:
            import statistics as _st
            gates.append(("T2 V_real test NLL(中位)< prior",
                          "PASS" if _st.median(tnlls) < 0.6931 else "FAIL"))
        swaps = [r[1]["domains"][d][sp]["side_swap_wdl_max_diff"]
                 for r in v_reports for d in r[1].get("domains", {})
                 for sp in r[1]["domains"][d]]
        gates.append(("T2 side-swap 数值一致",
                      "FAIL(maxΔ=%.3f,训练一致性损失下未完全收敛)" %
                      max(swaps) if swaps else "FAIL"))
    for r in p_reports:
        m = r[1].get("train_metrics", {}).get("validation")
        if m:
            gates.append(("T3 masked 零非法概率(seed %d)" % r[0],
                          "PASS" if m["illegal_prob_mass"] == 0 else "FAIL"))
    if arena:
        rej = arena.get("matchups", {}).get("total_rejections")
        gates.append(("T4 arena 零拒绝",
                      "PASS" if rej == 0 else "FAIL(%s)" % rej))
        bon = arena.get("matchups", {}).get("best_of_n")
        if bon:
            gates.append(("T4 best-of-N CI 下界 > 0",
                          "PASS" if bon.get("ci95", [1])[0] > 0 else "FAIL"))
    for g, c in gates:
        A("| %s | %s |" % (g, c))
    A("")

    # ---------------- run manifest (task §10)
    import glob as _glob
    try:
        dirty = bool(subprocess.run(["git", "status", "--porcelain"],
                                    capture_output=True, text=True,
                                    cwd=ROOT).stdout.strip())
        full_commit = subprocess.run(["git", "rev-parse", "HEAD"],
                                     capture_output=True, text=True,
                                     cwd=ROOT).stdout.strip()
    except OSError:
        dirty, full_commit = None, None
    import torch as _torch
    manifest = {
        "run_id": os.path.basename(rd.rstrip("/")),
        "git_commit": full_commit, "git_dirty": dirty,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version.split()[0],
        "torch": _torch.__version__,
        "cuda_available": _torch.cuda.is_available(),
        "gpu": [_torch.cuda.get_device_name(i)
                for i in range(_torch.cuda.is_count())]
        if _torch.cuda.is_available() else [],
        "artifacts": sorted(os.path.basename(p) for p in
                            _glob.glob(os.path.join(rd, "*.json"))),
        "datasets": sorted(os.path.basename(p) for p in
                           _glob.glob(os.path.join(rd, "datasets",
                                                   "*.jsonl.gz"))),
        "checkpoints": sorted(os.path.basename(p) for p in
                              _glob.glob(os.path.join(rd, "checkpoints",
                                                      "*.pt"))),
        "stop_reason": "completed",
    }
    with open(os.path.join(rd, "run_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, default=str)

    md = "\n".join(L)
    with open(os.path.join(rd, "report.md"), "w") as f:
        f.write(md + "\n")
    html = ["<html><head><meta charset='utf-8'><title>RL Phase 1</title>",
            "<style>body{font-family:sans-serif;max-width:1100px;"
            "margin:2em auto}table{border-collapse:collapse}"
            "td,th{border:1px solid #ccc;padding:4px 10px}</style></head>",
            "<body>"]
    for line in L:
        if line.startswith("|"):
            html.append("<p style='font-family:monospace'>%s</p>" % line)
        elif line.startswith("#"):
            html.append("<h2>%s</h2>" % line.lstrip("# "))
        else:
            html.append("<p>%s</p>" % line)
    html.append("</body></html>")
    with open(os.path.join(rd, "report.html"), "w") as f:
        f.write("\n".join(html))
    print(md[:2600])


if __name__ == "__main__":
    main()
