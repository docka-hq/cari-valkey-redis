#!/usr/bin/env python3
"""Post-hoc, from stored grader output: did search.py return the exact true order at all 5 places?

The grader passes an attempt on top-1 exact + >= 4 of the true 5 (room for an approximate index). This
reads the ids each attempt printed (grader_facts.queries[*].ids) and compares them with the exact cosine
top-5 in graders/vector_queries.json. It changes no verdict; it measures one thing the grader allowed.
"""
import json
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
truth = [q["top5"] for q in json.loads((ROOT / "graders/vector_queries.json").read_text())]
latest = {}
for f in sorted((ROOT / "runs/wave-1").glob("records-*.jsonl")):
    for line in f.read_text().splitlines():
        r = json.loads(line)
        if r.get("job") == "vector" and r.get("verdict") in ("pass", "fail"):
            latest[r["attempt_id"]] = r
out = defaultdict(lambda: {"attempts": 0, "all_exact": 0, "queries_exact": 0, "queries": 0})
rows = []
for aid, r in sorted(latest.items()):
    qs = (r.get("grader_facts") or {}).get("queries") or []
    exact = [q.get("ids") == truth[i] for i, q in enumerate(qs) if i < len(truth)]
    c = out[r["product"]]
    c["attempts"] += 1
    c["queries"] += len(exact)
    c["queries_exact"] += sum(exact)
    c["all_exact"] += int(bool(exact) and all(exact))
    rows.append({"attempt": aid, "product": r["product"], "lane": r["lane"], "verdict": r["verdict"],
                 "exact_order_queries": sum(exact), "of": len(exact)})
agg = ROOT / "runs/wave-1/aggregate"
(agg / "vector_order.json").write_text(json.dumps({"by_product": out, "attempts": rows}, indent=1))
print(json.dumps(out, indent=1))
for x in rows:
    if x["exact_order_queries"] != x["of"]:
        print("not exact:", x)
