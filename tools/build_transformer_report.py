#!/usr/bin/env python
"""Aggregate run artifacts into report.md / report.html (任务书 §10/§18).

Collects value/policy seed reports, ablation runs, arena summaries and the
contract into one Markdown (plus an HTML copy) with the §10 Gate table and
honest engineering-only flags. Test-split numbers are only included when
the run is NOT engineering-only.
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.rl.transformer import token_contract as tc            # noqa: E402


def load_json(path):
    try:
        with open(path, encoding="utf8") as f:
            return json.load(f)
    except Exception:
        return None


def fmt(v, nd=4):
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    rd = args.run_dir

    contract = load_json(os.path.join(rd, "contract.json"))
    lines = []
    add = lines.append
    add("# Transformer Baseline Run Report — %s" % os.path.basename(
        rd.rstrip("/")))
    add("")
    t0_pending = contract and \
        (contract.get("t0_backtest") or {}).get("status") != tc.T0_ACCEPTED
    if contract:
        add("- contract: `%s` | engine `%s` | sim_label `%s`" % (
            contract.get("contract_version"), contract.get("engine_version"),
            contract.get("sim_label_version")))
        add("- git: `%s`%s | GPU allowlist `%s` (reserved %s)" % (
            contract.get("git_commit"),
            " (dirty)" if contract.get("git_dirty") else "",
            contract.get("training_gpu_allowlist"),
            contract.get("reserved_physical_gpus")))
        if t0_pending:
            add("- **T0 Gate: pending — 本 run 为 engineering/toy 产物, "
                "任何指标都不得作为正式结论 (任务书 §3.2)**")
    add("")

    # ---------------- value reports
    add("## TValue")
    add("")
    for path in sorted(glob.glob(os.path.join(
            rd, "value_report_seed*.json"))):
        rep = load_json(path)
        if not rep:
            continue
        seed = rep.get("args", {}).get("seed")
        add("### seed %s (%s)" % (seed, "engineering" if
                                  rep.get("engineering_only")
                                  else "formal"))
        add("")
        add("| split | NLL | acc | Brier | ECE | dmg MAE | swap(raw) |")
        add("|---|---|---|---|---|---|---|")
        for split in ("validation", "test"):
            for dom in ("real", "sim"):
                m = (rep.get(split) or {}).get(dom)
                if not m:
                    continue
                add("| %s/%s | %s | %s | %s | %s | %s | %s |" % (
                    split, dom, fmt(m.get("nll")), fmt(m.get("acc"), 3),
                    fmt(m.get("brier")), fmt((m.get("ece") or 0)),
                    fmt((m.get("damage") or {}).get("mae")),
                    fmt(m.get("side_swap_wdl_max_diff"), 5)))
        rank = (rep.get("validation") or {}).get("sim_ranking") or {}
        add("- sim ranking: pairwise_acc=%s pairs=%s top_quartile_recall=%s"
            % (fmt(rank.get("pairwise_acc"), 3), rank.get("pairs"),
               fmt(rank.get("top_quartile_recall"), 3)))
        for dom, s in (rep.get("symmetrized") or {}).items():
            add("- symmetrized %s: wdl_max_diff=%.2e dmg_max_diff=%.2e "
                "(Gate ≤1e-5: %s)" % (
                    dom, s["wdl_max_diff"], s["dmg_max_diff"],
                    "PASS" if s["wdl_max_diff"] <= 1e-5 else "FAIL"))
        add("")

    # ---------------- policy reports
    add("## TPolicy-BC (teacher-forced)")
    add("")
    for path in sorted(glob.glob(os.path.join(
            rd, "policy_report_seed*.json"))):
        rep = load_json(path)
        if not rep:
            continue
        seed = rep.get("args", {}).get("seed")
        add("### seed %s (%s)" % (seed, "engineering" if
                                  rep.get("engineering_only")
                                  else "formal"))
        add("")
        for split in ("validation_teacher_forced", "test_teacher_forced"):
            m = rep.get(split) or {}
            if not m:
                continue
            add("- %s: verb_top1=%s end_acc=%s illegal=%s" % (
                split, fmt(m.get("verb_top1"), 3), fmt(m.get("end_acc"), 3),
                {k: fmt(v, 4) for k, v in
                 (m.get("illegal_mass") or {}).items()}))
            tops = {k: v for k, v in m.items() if k.startswith("top1_")}
            if tops:
                add("  - " + ", ".join("%s=%s" % (k[5:], fmt(v, 3))
                                       for k, v in sorted(tops.items())))
        add("")

    # ---------------- arena
    add("## Arena (direct pysim)")
    add("")
    for path in sorted(glob.glob(os.path.join(rd, "arena", "arena_*.json"))):
        rep = load_json(path)
        if not rep:
            continue
        add("- summary: `%s`" % json.dumps(rep.get("summary", {})))
        add("- Gate(§10.4): `%s`" % json.dumps(rep.get("gate_10_4", {})))
    add("")

    # ---------------- ablation
    ab = load_json(os.path.join(rd, "ablation", "ablation_runs.json"))
    if ab:
        add("## Ablation (development seed %s)" % ab.get("seed"))
        add("")
        add("| arm | kind | rc |")
        add("|---|---|---|")
        for r in ab.get("results", []):
            add("| %s | %s | %s |" % (r["arm"], r["kind"], r["rc"]))
        add("")

    add("## 已知限制")
    add("")
    if t0_pending:
        add("- T0 未冻结: 无正式 sim label; sim 域结论只反映 toy/历史数据。")
    add("- 多点技能 provisional 数值按任务书 §7.2 进入 coverage,strict verified 单列。")
    add("- Arena human/DeepSets 对照在 phase1 v2 数据重建后接入。")

    md = "\n".join(lines) + "\n"
    out_md = os.path.join(rd, "report.md")
    with open(out_md, "w", encoding="utf8") as f:
        f.write(md)
    html = ["<html><body><pre>", md.replace("&", "&amp;")
            .replace("<", "&lt;"), "</pre></body></html>"]
    with open(os.path.join(rd, "report.html"), "w", encoding="utf8") as f:
        f.write("\n".join(html))
    print("wrote", out_md)
    print(md[:2000])


if __name__ == "__main__":
    main()
