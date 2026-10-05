#!/usr/bin/env python3
"""Refill the selection cells whose answer came back EMPTY (a reasoning model spent the whole 16k output
budget thinking: finish=length, no text). Same prompt, same model, same route; only the output budget is
raised to 64k. An empty answer is not a result (it is never coded), so this fills a hole rather than
re-asking a question that got an answer. Records go to runs/<wave>/refill.jsonl with refill=true and the
budget used; kv_classify.py uses a refill answer only for a cell that has no non-empty original answer.
Issue 1 precedent: wave-2026-07-21 refilled 20 such cells at a higher cap, protocol otherwise unchanged.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("KV_HARNESS", "/opt/docka/harness-pg-eval"))
MAX_TOKENS = 64000


def ask(entry: dict, prompt: str) -> dict:
    from docka.agent import TransientProviderError, call_with_retry
    if entry["route"] == "anthropic":
        import anthropic
        c = anthropic.Anthropic(max_retries=0, timeout=1800)

        def _a():
            with c.messages.stream(model=entry["model"], max_tokens=MAX_TOKENS, messages=[{"role": "user", "content": prompt}],
                                   extra_body={"output_config": {"effort": entry.get("effort", "medium")}}) as s:
                return s.get_final_message()
        r = call_with_retry(_a, "anthropic stream")
        u, p = r.usage, entry["price"]
        return {"text": "\n".join(b.text for b in r.content if b.type == "text"), "finish": r.stop_reason,
                "tokens_in": u.input_tokens, "tokens_out": u.output_tokens,
                "cost_usd": round(((u.input_tokens or 0) * p["in"] + (u.output_tokens or 0) * p["out"]) / 1e6, 6),
                "cost_source": "usage x list price", "served_model": r.model, "provider": "Anthropic"}
    from openai import OpenAI
    c = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"], timeout=1800)

    def _o():
        r = c.chat.completions.create(model=entry["model"], messages=[{"role": "user", "content": prompt}],
                                      max_tokens=MAX_TOKENS, extra_body={"usage": {"include": True}})
        if not getattr(r, "choices", None) or r.choices[0].message is None:
            raise TransientProviderError("response without choices")
        return r
    r = call_with_retry(_o, "openrouter chat")
    u = r.usage
    det = getattr(u, "completion_tokens_details", None)
    cost = getattr(u, "cost", None) or (getattr(u, "model_extra", None) or {}).get("cost")
    return {"text": r.choices[0].message.content or "", "finish": r.choices[0].finish_reason,
            "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens,
            "tokens_reasoning": int(getattr(det, "reasoning_tokens", 0) or 0) if det else None,
            "cost_usd": round(float(cost), 6) if cost is not None else None,
            "cost_source": "openrouter metered" if cost is not None else "missing",
            "served_model": r.model, "provider": (getattr(r, "model_extra", None) or {}).get("provider")}


def main() -> None:
    cfg = json.loads((ROOT / "config/selection-1.json").read_text())
    wd = ROOT / "runs" / cfg["wave"]
    recs = [json.loads(x) for x in (wd / "records.jsonl").read_text().splitlines() if x.strip()]
    filled = {(r["scenario"], r["model_key"], r["rep"]) for r in recs if (r.get("text") or "").strip()}
    rf = wd / "refill.jsonl"
    if rf.exists():
        filled |= {(r["scenario"], r["model_key"], r["rep"]) for r in map(json.loads, rf.read_text().splitlines()) if (r.get("text") or "").strip()}
    holes = sorted({(r["scenario"], r["model_key"], r["rep"]) for r in recs} - filled)
    scen = {s["id"]: s for s in cfg["scenarios"]}
    park = {e["key"]: e for e in cfg["park"]}
    print(f"{len(holes)} empty cells to refill at max_tokens={MAX_TOKENS}: {holes}", flush=True)
    with rf.open("a") as out:
        for s, m, rep in holes:
            try:
                res = ask(park[m], scen[s]["prompt"])
                err = None if (res["text"] or "").strip() else f"empty again (finish={res['finish']})"
            except Exception as e:  # noqa: BLE001
                res, err = {}, f"{type(e).__name__}: {e}"[:500]
            rec = {"schema": "cari-kv/selection@v1", "wave": cfg["wave"], "refill": True, "max_tokens": MAX_TOKENS,
                   "scenario": s, "turf": scen[s]["turf"], "model_key": m, "model": park[m]["model"], "rep": rep, "error": err, **res}
            out.write(json.dumps(rec, sort_keys=True) + "\n")
            out.flush()
            print(f"{s} {m} r{rep}: {'ERR ' + err if err else 'ok'} ${rec.get('cost_usd')}", flush=True)


if __name__ == "__main__":
    main()
