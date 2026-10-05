#!/usr/bin/env python3
"""CARI Issue 2 - Selection: which in-memory store does a model pick when nobody names one?

Protocol (same as Issue 1): bare user prompt, no system prompt, single turn, provider-default sampling,
n=5 per model per scenario. Same five models as the execution wave; Claude Opus 5.5 runs with its
thinking on (it cannot be disabled) at the API's default effort, pinned explicitly.

  freeze      record config + this runner's hash before the first record
  run         all calls (resumable: a (scenario, model, rep) with a non-empty answer is never re-asked)
  classify    rule-based coding of every answer -> aggregate/summary.{json,md} + a review list
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("KV_HARNESS", "/opt/docka/harness-pg-eval"))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ----------------------------------------------------------------------------- calls

def ask_anthropic(entry: dict, prompt: str) -> dict:
    import anthropic
    from docka.agent import call_with_retry
    c = anthropic.Anthropic(max_retries=0, timeout=1200)

    def _req():
        with c.messages.stream(model=entry["model"], max_tokens=16000, messages=[{"role": "user", "content": prompt}],
                               extra_body={"output_config": {"effort": entry.get("effort", "medium")}}) as s:
            return s.get_final_message()

    r = call_with_retry(_req, "anthropic stream")
    u, p = r.usage, entry["price"]
    cost = ((u.input_tokens or 0) * p["in"] + (u.output_tokens or 0) * p["out"]) / 1e6
    return {"text": "\n".join(b.text for b in r.content if b.type == "text"), "finish": r.stop_reason,
            "tokens_in": u.input_tokens, "tokens_out": u.output_tokens, "tokens_reasoning": None,
            "cost_usd": round(cost, 6), "cost_source": "usage x list price", "served_model": r.model, "provider": "Anthropic"}


def ask_openrouter(entry: dict, prompt: str) -> dict:
    from openai import OpenAI
    from docka.agent import TransientProviderError, call_with_retry
    c = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])

    def _req():
        r = c.chat.completions.create(model=entry["model"], messages=[{"role": "user", "content": prompt}],
                                      max_tokens=16000, extra_body={"usage": {"include": True}})
        if not getattr(r, "choices", None) or r.choices[0].message is None:
            raise TransientProviderError("response without choices")
        return r

    r = call_with_retry(_req, "openrouter chat")
    u = r.usage
    det = getattr(u, "completion_tokens_details", None)
    cost = getattr(u, "cost", None)
    if cost is None:
        cost = (getattr(u, "model_extra", None) or {}).get("cost")
    return {"text": r.choices[0].message.content or "", "finish": r.choices[0].finish_reason,
            "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens,
            "tokens_reasoning": int(getattr(det, "reasoning_tokens", 0) or 0) if det else None,
            "cost_usd": round(float(cost), 6) if cost is not None else None,
            "cost_source": "openrouter metered" if cost is not None else "missing",
            "served_model": r.model, "provider": (getattr(r, "model_extra", None) or {}).get("provider")}


# ----------------------------------------------------------------------------- run

def records(wd: pathlib.Path) -> list[dict]:
    f = wd / "records.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def cmd_freeze(cfg: dict, cfgp: pathlib.Path) -> None:
    fz = pathlib.Path(str(cfgp) + ".frozen")
    if fz.exists():
        sys.exit(f"{fz} exists - never re-frozen; make a new wave")
    fz.write_text(json.dumps({"config_sha": sha(cfgp), "runner_sha": sha(pathlib.Path(__file__)), "frozen_at": now()}, indent=1) + "\n")
    print(fz.read_text())


def cmd_run(cfg: dict, cfgp: pathlib.Path) -> None:
    fz = json.loads(pathlib.Path(str(cfgp) + ".frozen").read_text())
    if fz["config_sha"] != sha(cfgp) or fz["runner_sha"] != sha(pathlib.Path(__file__)):
        sys.exit("inputs changed since freeze - a changed wave is a new wave")
    wd = ROOT / "runs" / cfg["wave"]
    wd.mkdir(parents=True, exist_ok=True)
    have = {(r["scenario"], r["model_key"], r["rep"]) for r in records(wd) if (r.get("text") or "").strip()}
    spent = sum(float(r.get("cost_usd") or 0) for r in records(wd))
    jobs = [(s, e, rep) for s in cfg["scenarios"] for e in cfg["park"] for rep in range(1, cfg["n"] + 1)
            if (s["id"], e["key"], rep) not in have]
    print(f"[{now()}] {len(jobs)} calls to make, ${spent:.2f} spent so far", flush=True)
    out = (wd / "records.jsonl").open("a")

    def one(job):
        s, e, rep = job
        try:
            res = (ask_anthropic if e["route"] == "anthropic" else ask_openrouter)(e, s["prompt"])
            err = None if (res["text"] or "").strip() else f"empty answer (finish={res['finish']})"
        except Exception as ex:  # noqa: BLE001 - a provider failure is recorded, not scored, and re-asked next run
            res, err = {}, f"{type(ex).__name__}: {ex}"[:500]
        return {"schema": "cari-kv/selection@v1", "wave": cfg["wave"], "scenario": s["id"], "turf": s["turf"],
                "model_key": e["key"], "model": e["model"], "rep": rep, "ts": now(), "error": err,
                "config_sha": fz["config_sha"], **res}

    with cf.ThreadPoolExecutor(max_workers=int(cfg.get("workers", 6))) as ex:
        for rec in ex.map(one, jobs):
            fcntl.flock(out, fcntl.LOCK_EX)
            out.write(json.dumps(rec, sort_keys=True) + "\n")
            out.flush()
            fcntl.flock(out, fcntl.LOCK_UN)
            spent += float(rec.get("cost_usd") or 0)
            print(f"[{now()}] {rec['scenario']:16} {rec['model_key']:12} r{rec['rep']} "
                  f"{'ERR ' + rec['error'] if rec['error'] else 'ok'}  ${spent:.3f}", flush=True)
            if spent > float(cfg["budget_stop_usd"]):
                print(f"[{now()}] BUDGET STOP at ${spent:.2f}", flush=True)
                os._exit(3)
    print(f"[{now()}] selection run complete, ${spent:.2f}", flush=True)


# ----------------------------------------------------------------------------- classify

# What the answer actually SETS UP, read from deployable artefacts first (image, engine, install,
# service name), then from the recommendation sentence. A client library is never a signal:
# `import redis` / redis-py talks to Valkey too.
INFRA = {
    "valkey": [r"valkey/valkey", r"\bimage:\s*['\"]?[\w./-]*valkey", r"\bvalkey-server\b", r"engine\s*[:=]\s*['\"]valkey",
               r"(apt|apt-get|brew|dnf|yum)\s+install[^\n]*\bvalkey\b", r"bitnami/valkey", r"valkey-bundle",
               r"elasticache[^\n]{0,40}\bvalkey\b", r"\bmemorystore for valkey\b",
               r"docker run[^\n]*\svalkey(/[\w-]+)?(:\S+)?(\s|$)"],
    "redis": [r"\bimage:\s*['\"]?[\w./-]*redis(?!insight)", r"redis/redis-stack", r"redis-stack-server", r"\bredis-server\b",
              r"engine\s*[:=]\s*['\"]redis", r"(apt|apt-get|brew|dnf|yum)\s+install[^\n]*\bredis\b", r"bitnami/redis",
              r"elasticache[^\n]{0,40}\bredis\b", r"\bredis cloud\b", r"\bupstash\b", r"\bmemorystore for redis\b",
              r"azure (cache|managed redis)", r"docker run[^\n]*\sredis(/redis-stack[\w-]*)?(:\S+)?(\s|$)"],
    "memcached": [r"\bimage:\s*['\"]?memcached", r"engine\s*[:=]\s*['\"]memcached", r"install[^\n]*\bmemcached\b",
                  r"docker run[^\n]*\smemcached(:\S+)?(\s|$)"],
}
PICK = re.compile(r"\b(use|using|choose|chose|pick|picked|recommend|go with|going with|i'd use|i would use|my pick|winner|answer)\b"
                  r"[^.\n]{0,60}?\b(valkey|redis|memcached|dragonfly|keydb|garnet|rabbitmq|sqs|postgres(?:ql)?|pgvector|qdrant|"
                  r"dynamodb|kafka|nats|celery|in-memory|lru)\b", re.I)


def classify(text: str) -> dict:
    t = text or ""
    first = {}
    for prod, pats in INFRA.items():
        pos = [m.start() for p in pats for m in re.finditer(p, t, re.I)]
        if pos:
            first[prod] = min(pos)
    infra = sorted(first, key=first.get)
    if infra:
        primary, basis = infra[0], "deployable artefact"
    else:
        m = PICK.search(t)
        word = m.group(2).lower() if m else None
        primary = {"postgresql": "postgres"}.get(word, word) if word else "none"
        if primary not in ("valkey", "redis", "memcached"):
            primary = "other" if word else "none"
        basis = "recommendation sentence" if m else "no pick found"
    return {"primary": primary, "basis": basis, "infra_products": infra,
            "mentions_valkey": bool(re.search(r"\bvalkey\b", t, re.I)),
            "mentions_redis": bool(re.search(r"\bredis\b", t, re.I)),
            "conflict": len(infra) > 1}


def cmd_classify(cfg: dict, _p) -> None:
    wd = ROOT / "runs" / cfg["wave"]
    recs = {}
    for r in records(wd):
        if (r.get("text") or "").strip():
            recs[(r["scenario"], r["model_key"], r["rep"])] = r  # latest answer per cell
    overrides = {}
    ovp = ROOT / "config" / f"{cfg['wave']}-review.json"
    if ovp.exists():
        overrides = json.loads(ovp.read_text())  # hand review of conflicts: {"scenario|model|rep": "redis"}
    rows = []
    for (s, m, rep), r in sorted(recs.items()):
        c = classify(r["text"])
        key = f"{s}|{m}|{rep}"
        if key in overrides:
            c["primary"], c["basis"] = overrides[key]["primary"], "hand review: " + overrides[key]["why"]
        rows.append({"scenario": s, "model_key": m, "rep": rep, **c, "cost_usd": r.get("cost_usd")})
    agg = wd / "aggregate"
    agg.mkdir(exist_ok=True)
    (agg / "classified.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    by_model = defaultdict(Counter)
    by_scen = defaultdict(Counter)
    for x in rows:
        by_model[x["model_key"]][x["primary"]] += 1
        by_scen[x["scenario"]][x["primary"]] += 1
    tot = Counter(x["primary"] for x in rows)
    mv = sum(x["mentions_valkey"] for x in rows)
    review = [x for x in rows if x["conflict"] or x["basis"] != "deployable artefact"]
    summary = {"answers": len(rows), "primary": dict(tot), "mentions_valkey": mv,
               "by_model": {k: dict(v) for k, v in by_model.items()}, "by_scenario": {k: dict(v) for k, v in by_scen.items()},
               "needs_review": len(review), "spend_usd": round(sum(float(x.get("cost_usd") or 0) for x in rows), 3)}
    (agg / "summary.json").write_text(json.dumps(summary, indent=1))
    cols = ["redis", "valkey", "memcached", "other", "none"]
    L = [f"# {cfg['wave']} - which in-memory store does the model pick?", "",
         f"{len(rows)} answers. Valkey mentioned at all: {mv}/{len(rows)}. Spend ${summary['spend_usd']}.", "",
         "| model | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for e in cfg["park"]:
        L.append(f"| {e['model']} | " + " | ".join(str(by_model[e['key']].get(c, 0)) for c in cols) + " |")
    L.append("| **all** | " + " | ".join(f"**{tot.get(c, 0)}**" for c in cols) + " |")
    L += ["", "| scenario | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for s in cfg["scenarios"]:
        L.append(f"| {s['id']} | " + " | ".join(str(by_scen[s['id']].get(c, 0)) for c in cols) + " |")
    L += ["", f"## Needs review ({len(review)}): conflicting artefacts or no artefact", ""]
    L += [f"- {x['scenario']}|{x['model_key']}|{x['rep']}: primary={x['primary']} basis={x['basis']} infra={x['infra_products']}"
          for x in review]
    (agg / "summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["freeze", "run", "classify"])
    ap.add_argument("--config", default=str(ROOT / "config/selection-1.json"))
    a = ap.parse_args()
    cfgp = pathlib.Path(a.config).resolve()
    cfg = json.loads(cfgp.read_text())
    {"freeze": cmd_freeze, "run": cmd_run, "classify": cmd_classify}[a.cmd](cfg, cfgp)


if __name__ == "__main__":
    main()
