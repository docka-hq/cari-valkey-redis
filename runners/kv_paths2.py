#!/usr/bin/env python3
"""Wave-2 (Redis 8.10 -> Valkey 9.1): how each attempt met the wall, read from tool_log.json, where every
command sits next to the output the agent actually saw. Output: runs/wave-2/aggregate/paths.{jsonl,md}.

Signals (each counted once per attempt):
  migration_guide    fetched valkey.io/topics/migration
  restore_error      an output contained "DUMP payload version or checksum are wrong"
  replicaof_ok       ran REPLICAOF/SLAVEOF against the new server and got OK back
  link_down_seen     an output showed master_link_status:down
  rdb_source         fetched Redis/Valkey source code (rdb.c, t_stream.c, ...) from GitHub
  payload_patching   rewrote DUMP payloads or RDB bytes (version/checksum/crc64 edits)
  logical_copy       wrote keys by type (HSET/RPUSH/ZADD/XADD/SET ... over a SCAN of the old server)
"""
from __future__ import annotations

import json
import pathlib
import re
import statistics
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
WD = ROOT / "runs/wave-2"


def main() -> None:
    latest = {}
    for f in sorted(WD.glob("records-*.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r.get("verdict") in ("pass", "fail"):
                latest[r["attempt_id"]] = r
    rows = []
    for aid, r in sorted(latest.items()):
        log = json.loads((WD / "attempts" / aid / "tool_log.json").read_text())
        ins = [json.dumps(e.get("input") or {}) for e in log]
        outs = [e.get("output") or "" for e in log]
        both = list(zip(ins, outs))
        urls = [(e.get("input") or {}).get("url", "") for e in log if e.get("tool") == "fetch_doc"]
        sig = {
            "migration_guide": any("valkey.io/topics/migration" in u for u in urls)
                               or any("valkey.io/topics/migration" in i for i in ins),
            "restore_error": any("DUMP payload version or checksum are wrong" in o for o in outs),
            "replicaof_ok": any(re.search(r"(?i)\b(replicaof|slaveof)\b[\s'\",]+(127\.0\.0\.1|localhost)[\s'\",]+6379", i)
                                and re.search(r"stdout:\s*\n?\s*(b')?OK", o) for i, o in both),
            "link_down_seen": any("master_link_status:down" in o for o in outs),
            "rdb_source": any(re.search(r"(?i)(githubusercontent\.com|api\.github\.com|github\.com/(valkey-io|redis))[^\"']*"
                                        r"(rdb|t_stream|listpack|ziplist|intset|lzf)", i + " " + u) for i, u in zip(ins, urls + [""] * len(ins))),
            "payload_patching": any(re.search(r"(?i)crc64|rdb_?version|RDB_VERSION|payload\[|\.dump['\"]?\)|version byte|patch(ed)?_payload", i)
                                    for i in ins),
            "logical_copy": any(re.search(r"(?i)scan", i) and re.search(r"(?i)\b(hset|rpush|zadd|xadd|sadd)\b", i) for i in ins),
        }
        rows.append({"attempt": aid, "lane": r["lane"], "rep": r["rep"], "verdict": r["verdict"], "stop": r.get("stop_reason"),
                     "tool_calls": r.get("tool_calls"), "tokens": r.get("tokens"), "cost_usd": r.get("cost_usd"),
                     "wall_min": round((r.get("wall_seconds") or 0) / 60, 1), "reasons": r.get("grader_reasons") or [],
                     **sig})
    (WD / "aggregate").mkdir(exist_ok=True)
    (WD / "aggregate/paths.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    keys = ["migration_guide", "restore_error", "replicaof_ok", "link_down_seen", "rdb_source", "payload_patching", "logical_copy"]
    L = [f"# wave-2 paths ({len(rows)} graded attempts)", "",
         "| attempt | verdict | stop | calls | tokens | min | " + " | ".join(keys) + " |",
         "|---|---|---|---|---|---|" + "---|" * len(keys)]
    for x in rows:
        L.append(f"| {x['lane']} r{x['rep']} | {x['verdict']} | {x['stop']} | {x['tool_calls']} | {x['tokens']} | {x['wall_min']} | "
                 + " | ".join("x" if x[k] else "" for k in keys) + " |")
    by = defaultdict(list)
    for x in rows:
        by[x["lane"]].append(x)
    L += ["", "| model | pass | median calls | median tokens | median min | spend $ |", "|---|---|---|---|---|---|"]
    for lane, xs in by.items():
        L.append(f"| {lane} | {sum(x['verdict'] == 'pass' for x in xs)}/{len(xs)} | {statistics.median([x['tool_calls'] or 0 for x in xs])} | "
                 f"{statistics.median([x['tokens'] or 0 for x in xs])} | {statistics.median([x['wall_min'] for x in xs])} | "
                 f"{round(sum(float(x['cost_usd'] or 0) for x in xs), 3)} |")
    L += ["", "## failure reasons", ""] + [f"- {x['lane']} r{x['rep']}: {'; '.join(x['reasons'])[:400]}" for x in rows if x["verdict"] == "fail"]
    (WD / "aggregate/paths.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
