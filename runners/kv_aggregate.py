#!/usr/bin/env python3
"""Aggregate one wave of kv_wave records -> runs/<wave>/aggregate/summary.{json,md}.

Regenerable from the records at any time; the records are never edited. Latest DEFINITIVE record per
attempt (pass/fail) counts; error records are reported separately and never scored.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
JOBS = ["cache", "vector", "migrate"]
PRODS = ["valkey", "redis"]


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/wave-1.json"))
    a = ap.parse_args()
    cfg = json.loads(pathlib.Path(a.config).read_text())
    wd = ROOT / "runs" / cfg["wave"]
    recs = []
    for f in sorted(wd.glob("records-*.jsonl")):
        recs += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    latest, errors = {}, []
    for r in recs:
        if r.get("verdict") in ("pass", "fail"):
            latest[r["attempt_id"]] = r
        else:
            errors.append(r)
    lanes = [e["key"] for e in cfg["park"]]
    models = {e["key"]: e["model"] for e in cfg["park"]}

    cell = defaultdict(list)
    for r in latest.values():
        cell[(r["lane"], r["job"], r["product"])].append(r)

    out = {"wave": cfg["wave"], "attempts_definitive": len(latest), "error_records": len(errors),
           "spend_usd_all_records": round(sum(float(r.get("cost_usd") or 0) for r in recs), 2),
           "matrix": {}, "by_job_product": {}, "by_product": {}, "by_lane": {}, "stop_reasons": {},
           "failure_reasons": {}, "cost": {}}
    for lane in lanes:
        out["matrix"][lane] = {}
        for job in JOBS:
            for p in PRODS:
                rs = cell[(lane, job, p)]
                out["matrix"][lane][f"{job}_{p}"] = {
                    "pass": sum(r["verdict"] == "pass" for r in rs), "n": len(rs),
                    "median_tool_calls": med([r.get("tool_calls") for r in rs]),
                    "median_tokens": med([r.get("tokens") for r in rs]),
                    "median_cost_usd": med([r.get("cost_usd") for r in rs]),
                    "median_wall_s": med([r.get("wall_seconds") for r in rs]),
                    "stops": dict(Counter(r.get("stop_reason") for r in rs)),
                }
    for job in JOBS:
        for p in PRODS:
            rs = [r for r in latest.values() if r["job"] == job and r["product"] == p]
            out["by_job_product"][f"{job}_{p}"] = {"pass": sum(r["verdict"] == "pass" for r in rs), "n": len(rs),
                                                    "median_tool_calls": med([r.get("tool_calls") for r in rs]),
                                                    "median_tokens": med([r.get("tokens") for r in rs]),
                                                    "median_cost_usd": med([r.get("cost_usd") for r in rs])}
    for p in PRODS:
        rs = [r for r in latest.values() if r["product"] == p]
        out["by_product"][p] = {"pass": sum(r["verdict"] == "pass" for r in rs), "n": len(rs)}
    for lane in lanes:
        rs = [r for r in latest.values() if r["lane"] == lane]
        out["by_lane"][lane] = {"model": models[lane], "pass": sum(r["verdict"] == "pass" for r in rs), "n": len(rs),
                                "spend_usd": round(sum(float(r.get("cost_usd") or 0) for r in recs if r.get("lane") == lane), 2),
                                "providers": sorted({pv for r in rs for pv in (r.get("providers") or [])})}
    out["stop_reasons"] = dict(Counter(r.get("stop_reason") for r in latest.values()))
    fr = defaultdict(list)
    for r in latest.values():
        if r["verdict"] == "fail":
            for reason in (r.get("grader_reasons") or ["(no reason recorded)"]):
                fr[f"{r['job']}_{r['product']}"].append({"attempt": r["attempt_id"], "reason": reason[:300]})
    out["failure_reasons"] = fr
    out["errors"] = [{"attempt": r["attempt_id"], "error": r.get("error"), "stop": r.get("stop_reason")} for r in errors]

    agg = wd / "aggregate"
    agg.mkdir(exist_ok=True)
    (agg / "summary.json").write_text(json.dumps(out, indent=1, default=str))

    L = [f"# {cfg['wave']} - Valkey 9.1 vs Redis 8.10", "",
         f"Definitive attempts: {len(latest)} / {len(lanes) * 18}. Error records (not scored): {len(errors)}. "
         f"Spend, all records: ${out['spend_usd_all_records']}.", "",
         "## Pass matrix (n per cell = 3)", "",
         "| model | " + " | ".join(f"{j} {p}" for j in JOBS for p in PRODS) + " | total |",
         "|---|" + "---|" * (len(JOBS) * len(PRODS) + 1)]
    for lane in lanes:
        row = [f"{c['pass']}/{c['n']}" for c in (out["matrix"][lane][f"{j}_{p}"] for j in JOBS for p in PRODS)]
        L.append(f"| {models[lane]} | " + " | ".join(row) + f" | {out['by_lane'][lane]['pass']}/{out['by_lane'][lane]['n']} |")
    L.append("| **all** | " + " | ".join(f"**{out['by_job_product'][f'{j}_{p}']['pass']}/{out['by_job_product'][f'{j}_{p}']['n']}**"
                                      for j in JOBS for p in PRODS) + " | |")
    L += ["", "## Effort per job and product (medians over all models)", "",
          "| job | product | pass | tool calls | tokens | cost $ |", "|---|---|---|---|---|---|"]
    for j in JOBS:
        for p in PRODS:
            c = out["by_job_product"][f"{j}_{p}"]
            L.append(f"| {j} | {p} | {c['pass']}/{c['n']} | {c['median_tool_calls']} | {c['median_tokens']} | "
                     f"{c['median_cost_usd'] if c['median_cost_usd'] is None else round(c['median_cost_usd'], 4)} |")
    L += ["", "## Stop reasons", "", "```", json.dumps(out["stop_reasons"]), "```", "", "## Failure reasons", ""]
    for k, v in sorted(fr.items()):
        L.append(f"### {k}")
        for x in v:
            L.append(f"- `{x['attempt']}`: {x['reason']}")
        L.append("")
    if errors:
        L += ["## Error records (not scored)", ""] + [f"- `{e['attempt']}`: {e['error']}" for e in out["errors"]]
    (agg / "summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
