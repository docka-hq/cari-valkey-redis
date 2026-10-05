#!/usr/bin/env python3
"""How the agents got there: path features per attempt, from what the records keep.

wave-1 stores the agent's commands (commands.json), its own words and tool calls (transcript.json),
and the documentation it fetched (record.doc_fetches). It does NOT store tool outputs, so errors are
read from the agent's own words, never inferred from exit codes. Output: runs/<wave>/aggregate/paths.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
from collections import Counter, defaultdict
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent

FEATURES = {
    # Redis/Valkey commands are matched in UPPER case (how they are written in commands and code); client
    # methods in lower case with their receiver's call syntax. `json.dumps` must never count as DUMP.
    # client library actually used in code / installed
    "client_redis_py": r"(?m)^\s*(import redis\b|from redis\b)|redis\.Redis\(|redis\.StrictRedis\(",
    "client_valkey_py": r"(?m)^\s*(import valkey\b|from valkey\b)|valkey\.Valkey\(",
    "client_glide": r"(?i)\bglide\b",
    # vector job
    "ft_create": r"FT\.CREATE|\.create_index\(",
    "ft_search": r"FT\.SEARCH|FT\.AGGREGATE|\.ft\([^)]*\)\.search\(",
    "vector_sets": r"\bVADD\b|\bVSIM\b|\.vadd\(|\.vsim\(",
    "hnsw": r"\bHNSW\b",
    "flat_index": r"\bVECTOR\b[^\n]{0,20}\bFLAT\b|['\"]FLAT['\"]",
    "on_json": r"ON\s+JSON|JSON\.SET|\.json\(\)\.set\(",
    "resp2_forced": r"protocol\s*=\s*2",
    "module_list": r"(?i)MODULE\s+LIST|INFO\s+modules|module_list\(",
    # migration job
    "replicaof": r"(?i)\bREPLICAOF\b|\bSLAVEOF\b|\.replicaof\(|\.slaveof\(",
    "dump_restore": r"\bRESTORE\b|\.restore\(",
    "migrate_cmd": r"\bMIGRATE\b",
    "rdb_file": r"dump\.rdb|--rdb\b|\bBGSAVE\b",
    "uses_old_cli": r"/opt/redis-7\.2/bin/redis-cli",
    # cache job
    "ttl_set": r"\.setex\(|\bex\s*=|\bSETEX\b|\bEX\b|\bpx\s*=|\.expire\(",
    "delete_on_update": r"\.delete\(|\.unlink\(|\bDEL\b",
}
TROUBLE = re.compile(r"(not supported|unsupported|unknown command|unknown argument|doesn't support|does not support|"
                     r"isn't supported|is not supported|syntax error|parse error|ResponseError|Traceback|failed|error)", re.I)


def domain(u: str) -> str:
    try:
        h = urlparse(u).hostname or ""
    except Exception:  # noqa: BLE001
        return "?"
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/wave-1.json"))
    a = ap.parse_args()
    cfg = json.loads(pathlib.Path(a.config).read_text())
    wd = ROOT / "runs" / cfg["wave"]
    latest = {}
    for f in sorted(wd.glob("records-*.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r.get("verdict") in ("pass", "fail"):
                latest[r["attempt_id"]] = r
    rows = []
    for aid, r in sorted(latest.items()):
        ad = wd / "attempts" / aid
        try:
            cmds = json.loads((ad / "commands.json").read_text())
            tr = json.loads((ad / "transcript.json").read_text())
        except OSError:
            continue
        blob = "\n".join(cmds)
        words = "\n".join(t.get("text") or "" for t in tr)
        feats = {k: bool(re.search(p, blob)) for k, p in FEATURES.items()}
        curl_urls = re.findall(r"https?://[^\s'\"<>)]+", "\n".join(c for c in cmds if re.search(r"\b(curl|wget)\b", c)))
        docs = [domain(u) for u in (r.get("doc_fetches") or [])] + [domain(u) for u in curl_urls if "127.0.0.1" not in u]
        rows.append({
            "attempt": aid, "lane": r["lane"], "job": r["job"], "product": r["product"], "verdict": r["verdict"],
            "tool_calls": r.get("tool_calls"), "tokens": r.get("tokens"), "cost_usd": r.get("cost_usd"),
            "doc_domains": sorted(set(docs)), "doc_fetch_count": len(docs),
            "trouble_mentions": len(TROUBLE.findall(words)), **feats,
        })
    agg = wd / "aggregate"
    agg.mkdir(exist_ok=True)
    (agg / "paths.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))

    def cell(job, prod):
        return [x for x in rows if x["job"] == job and x["product"] == prod]

    L = [f"# {cfg['wave']} - how the agents got there ({len(rows)} graded attempts)", "",
         "Tool outputs are not stored in wave-1 records; 'trouble words' counts the agent's own mentions of errors.", ""]
    for job in ("cache", "vector", "migrate"):
        L += [f"## {job}", "", "| | valkey | redis |", "|---|---|---|"]
        cv, cr = cell(job, "valkey"), cell(job, "redis")
        L.append(f"| attempts | {len(cv)} | {len(cr)} |")
        for metric in ("tool_calls", "tokens", "trouble_mentions", "doc_fetch_count"):
            mv = statistics.median([x[metric] or 0 for x in cv]) if cv else None
            mr = statistics.median([x[metric] or 0 for x in cr]) if cr else None
            L.append(f"| median {metric} | {mv} | {mr} |")
        for k in FEATURES:
            nv, nr = sum(x[k] for x in cv), sum(x[k] for x in cr)
            if nv or nr:
                L.append(f"| {k} | {nv}/{len(cv)} | {nr}/{len(cr)} |")
        dv = Counter(d for x in cv for d in x["doc_domains"])
        dr = Counter(d for x in cr for d in x["doc_domains"])
        L.append(f"| docs read (attempts per domain) | {dict(dv.most_common(6))} | {dict(dr.most_common(6))} |")
        L.append("")
    by_lane = defaultdict(list)
    for x in rows:
        by_lane[(x["lane"], x["product"])].append(x["tool_calls"] or 0)
    L += ["## median tool calls per model and product", "", "| model | valkey | redis |", "|---|---|---|"]
    for e in cfg["park"]:
        v = by_lane[(e["key"], "valkey")]
        rr = by_lane[(e["key"], "redis")]
        L.append(f"| {e['model']} | {statistics.median(v) if v else '-'} | {statistics.median(rr) if rr else '-'} |")
    (agg / "paths.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
