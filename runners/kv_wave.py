#!/usr/bin/env python3
"""kv_wave - execution runner for CARI Issue 2: Valkey vs Redis, same jobs, same agents.

Agent loop, sandbox and doc fetching come from the Docka harness, branch `pg-eval` (empty or cut-off
turns are not completions; provider failures are their own stop reason; transient provider errors are
retried inside the turn). Two things are added here and nowhere else:

  * AnthropicAdaptiveClient - Claude Opus 5.5 cannot run with thinking disabled (a 400 at every effort
    level), which is what the harness's Anthropic client sends. This client leaves thinking on, pins
    effort explicitly (the API default, `medium`), echoes the assistant content back VERBATIM so the
    thinking blocks stay valid, caches the prefix, and streams (a 64k per-turn limit needs streaming).
  * OpenRouterMeteredClient - the harness's OpenRouter client, plus the provider's metered cost and
    the served model/provider per turn.

Subcommands
  ping                      one two-turn tool-use round trip per park model (route, served model, cost)
  proof --task T [--solution FILE] --expect pass|fail      grader proof in a fresh container
  freeze                    record the config/tasks/runner/image hashes before the first record
  plan                      list the wave's attempts
  run --lane KEY            run one model's attempts (resumable; one process per lane)
  status                    progress, verdicts and spend per lane

No per-run caps besides a far-away runaway guard (recorded as such). The wave budget is checked
between attempts: at the stop line the lane exits and a human decides.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("KV_HARNESS", "/opt/docka/harness-pg-eval"))

from docka.agent import (  # noqa: E402
    AgentRunner, LLMTurn, OpenRouterClient, _utcnow, build_prompt, call_with_retry,
)
from docka.docfetch import DocFetchService  # noqa: E402
from docka.models import AgentAttempt  # noqa: E402
from docka.sandbox import SandboxManager  # noqa: E402
from docka.tasks import TaskSuiteLoader  # noqa: E402

NUDGE = ("[Your previous turn contained no tool call and ended at the per-turn output limit or empty. "
         "Continue the job with the next tool call, or state clearly that the task is complete.]")


# ----------------------------------------------------------------------------- model clients

class AnthropicAdaptiveClient:
    def __init__(self, model: str, max_tokens: int, effort: str, price: dict) -> None:
        import anthropic
        self._c = anthropic.Anthropic(max_retries=0, timeout=1200)
        self.model, self.max_tokens, self.effort, self.price = model, max_tokens, effort, price
        self.usage_log: list[dict] = []

    def turn(self, system: str, tools: list[dict], messages: list[dict]) -> LLMTurn:
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        tools2 = [dict(t) for t in tools]
        tools2[-1] = {**tools2[-1], "cache_control": {"type": "ephemeral"}}
        msgs = list(messages)
        last = msgs[-1]
        content = last["content"]
        content = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
        if content and isinstance(content[-1], dict):
            content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
        msgs[-1] = {"role": last["role"], "content": content}

        def _req():
            with self._c.messages.stream(model=self.model, max_tokens=self.max_tokens, system=sys_blocks,
                                         tools=tools2, messages=msgs,
                                         extra_body={"output_config": {"effort": self.effort}}) as s:
                return s.get_final_message()

        resp = call_with_retry(_req, "anthropic messages.stream")
        u = resp.usage
        ui, uo = int(u.input_tokens or 0), int(u.output_tokens or 0)
        cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        p = self.price
        cost = (ui * p["in"] + cw * p["cache_write"] + cr * p["cache_read"] + uo * p["out"]) / 1e6
        self.usage_log.append({"in": ui, "cache_write": cw, "cache_read": cr, "out": uo, "cost": cost,
                               "cost_source": "usage x list price", "model": resp.model, "stop": resp.stop_reason})
        text = "\n".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        turn = LLMTurn(text=text, tool_calls=calls, stop_reason=resp.stop_reason or "end_turn",
                       tokens=ui + cw + cr + uo, tokens_in=ui + cw + cr, tokens_out=uo)
        turn.raw_assistant_content = list(resp.content)  # thinking blocks must go back unchanged
        return turn


class OpenRouterMeteredClient(OpenRouterClient):
    def __init__(self, model: str, max_tokens: int, price: dict) -> None:
        super().__init__(model, max_tokens=max_tokens)
        self.extra_body_base = {"extra_body": {"usage": {"include": True}}}
        self.price = price
        self.usage_log: list[dict] = []
        self._last = None
        orig = self._c.chat.completions.create

        def create(*a, **k):
            r = orig(*a, **k)
            self._last = r
            return r

        self._c.chat.completions.create = create

    def turn(self, system: str, tools: list[dict], messages: list[dict]) -> LLMTurn:
        self._last = None
        t = super().turn(system, tools, messages)
        r = self._last
        u = getattr(r, "usage", None)
        cost = getattr(u, "cost", None) if u is not None else None
        if cost is None and u is not None:
            cost = (getattr(u, "model_extra", None) or {}).get("cost")
        det = getattr(u, "prompt_tokens_details", None) if u is not None else None
        cached = int(getattr(det, "cached_tokens", 0) or 0) if det is not None else 0
        est = (t.tokens_in * self.price["in"] + t.tokens_out * self.price["out"]) / 1e6
        self.usage_log.append({
            "in": t.tokens_in, "out": t.tokens_out, "reasoning": t.tokens_reasoning, "cached": cached,
            "cost": float(cost) if cost is not None else est,
            "cost_source": "openrouter metered" if cost is not None else "usage x list price (no metered cost returned)",
            "model": getattr(r, "model", None), "provider": (getattr(r, "model_extra", None) or {}).get("provider"),
            "stop": t.stop_reason,
        })
        return t


def make_client(entry: dict, cfg: dict):
    mt = int(cfg["per_turn_max_tokens"])
    if entry["route"] == "anthropic":
        return AnthropicAdaptiveClient(entry["model"], mt, entry.get("effort", "medium"), entry["price"])
    if entry["route"] == "openrouter":
        return OpenRouterMeteredClient(entry["model"], mt, entry["price"])
    raise ValueError(f"unknown route {entry['route']}")


# ----------------------------------------------------------------------------- agent loop

class KVAgentRunner(AgentRunner):
    """The pg-eval loop with two additions: assistant content goes back verbatim when the client
    supplies it (Anthropic thinking blocks), and a `refusal` stop reason ends the attempt as such."""

    def run(self, task, doc, sandbox, mode: str = "doc", on_progress=None, dk=None) -> AgentAttempt:
        attempt = AgentAttempt()
        system, tools, first_user = build_prompt(task, mode)
        allowed = {t["name"] for t in tools}
        messages: list[dict] = [{"role": "user", "content": first_user}]
        tool_calls_made = empty_turns = turns = 0
        t0 = self._clock()
        attempt.t0 = _utcnow()
        while True:
            if self._over_wall_clock(t0):
                attempt.completed, attempt.stop_reason = False, "wall_clock"
                break
            try:
                turn = self.client.turn(system, tools, messages)
            except Exception as e:  # noqa: BLE001
                attempt.completed, attempt.stop_reason = False, "provider_error"
                attempt.error = f"{type(e).__name__}: {e}"[:500]
                attempt.transcript.append({"role": "assistant", "text": "", "tool_calls": [],
                                           "stop": "provider_error", "error": attempt.error})
                break
            turns += 1
            attempt.tokens += turn.tokens
            attempt.tokens_reasoning += int(getattr(turn, "tokens_reasoning", 0) or 0)
            if turn.tokens_in or turn.tokens_out:
                attempt.tokens_in += turn.tokens_in
                attempt.tokens_out += turn.tokens_out
            else:
                attempt.tokens_in += turn.tokens
            attempt.transcript.append({"role": "assistant", "text": turn.text,
                                       "tool_calls": [{k: v for k, v in tc.items() if k != "thought_signature"}
                                                      for tc in turn.tool_calls],
                                       "stop": turn.stop_reason})
            raw = getattr(turn, "raw_assistant_content", None)
            if turn.stop_reason == "refusal":
                attempt.completed, attempt.stop_reason = False, "refusal"
                break
            if not turn.tool_calls:
                cut = turn.stop_reason in ("length", "max_tokens") or not (turn.text or "").strip()
                if cut and empty_turns == 0:
                    empty_turns += 1
                    if raw:
                        has_visible = any(getattr(b, "type", None) in ("text", "tool_use") for b in raw)
                        content = list(raw) + ([] if has_visible else [{"type": "text", "text": "(no output)"}])
                    else:
                        content = [{"type": "text", "text": turn.text or "(empty)"}]
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": [{"type": "text", "text": NUDGE}]})
                    continue
                if cut:
                    attempt.completed, attempt.stop_reason = False, "turn_cap"
                    break
                attempt.completed, attempt.stop_reason = True, "completed"
                break
            empty_turns = 0
            if raw is not None:
                assistant_content = list(raw)
            else:
                assistant_content = ([{"type": "text", "text": turn.text}] if turn.text else []) + [
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
                    for tc in turn.tool_calls]
            messages.append({"role": "assistant", "content": assistant_content})
            results = []
            for tc in turn.tool_calls:
                tool_calls_made += 1
                attempt.tool_calls = tool_calls_made
                out = self._dispatch(tc, doc, sandbox, attempt, allowed)
                results.append({"type": "tool_result", "tool_use_id": tc["id"], "content": out})
            messages.append({"role": "user", "content": results})
            if self.max_tokens and attempt.tokens >= self.max_tokens:
                attempt.completed, attempt.stop_reason = False, "token_cap"
                break
            if tool_calls_made >= self.max_tool_calls:
                attempt.completed, attempt.stop_reason = False, "tool_call_cap"
                break
        attempt.wall_seconds = round(self._clock() - t0, 2)
        attempt.t1 = _utcnow()
        attempt.doc_fetches = doc.retrievals()
        attempt.turns = turns
        return attempt


# ----------------------------------------------------------------------------- config, records

def load_cfg(path: str) -> dict:
    cfg = json.loads(pathlib.Path(path).read_text())
    cfg["_path"] = str(pathlib.Path(path).resolve())
    return cfg


def file_sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fingerprint(cfg: dict) -> dict:
    tasks = {p.name: file_sha(p) for p in sorted((ROOT / cfg["tasks_dir"]).glob("*.yaml"))}
    return {
        "config_sha": file_sha(pathlib.Path(cfg["_path"])),
        "tasks_sha": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest(),
        "runner_sha": file_sha(pathlib.Path(__file__)),
        "harness_commit": harness_commit(),
        "images": {t: image_id(t) for t in cfg["images"]},
    }


def harness_commit() -> str:
    h = pathlib.Path(os.environ.get("KV_HARNESS", "/opt/docka/harness-pg-eval"))
    try:
        head = (h / ".git/HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            p = h / ".git" / ref
            if p.exists():
                return p.read_text().strip()
            for line in (h / ".git/packed-refs").read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split()[0]
        return head
    except OSError:
        return "unknown"


def image_id(tag: str) -> str:
    p = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", tag], capture_output=True, text=True)
    return p.stdout.strip() or "missing"


def wave_dir(cfg: dict) -> pathlib.Path:
    d = ROOT / "runs" / cfg["wave"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_records(cfg: dict) -> list[dict]:
    out = []
    for f in sorted(wave_dir(cfg).glob("records-*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def append_record(cfg: dict, lane: str, rec: dict) -> None:
    with (wave_dir(cfg) / f"records-{lane}.jsonl").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def plan(cfg: dict) -> list[dict]:
    jobs = ["cache", "vector", "migrate"]
    out = []
    for entry in cfg["park"]:
        for rep in range(1, int(cfg["reps"]) + 1):
            order = ["valkey", "redis"] if rep % 2 == 1 else ["redis", "valkey"]
            for job in jobs:
                for prod in order:
                    tid = f"{job}_{prod}"
                    out.append({"attempt_id": f"{cfg['wave']}__{tid}__{entry['key']}__r{rep}", "task": tid,
                                "job": job, "product": prod, "lane": entry["key"], "rep": rep})
    return out


# ----------------------------------------------------------------------------- one attempt

ARTIFACT_CMDS = {
    "cache": ["cat /app/catalog.py", "ls -la /app"],
    "vector": ["cat /app/search.py", "ls -la /app", "$CLI -p 6379 FT._LIST", "$CLI -p 6379 INFO keyspace",
               "$CLI -p 6379 DBSIZE"],
    "migrate": ["$CLI -p 6380 INFO replication", "$CLI -p 6380 INFO keyspace",
                "/opt/redis-7.2/bin/redis-cli -p 6379 INFO keyspace", "cat $CONF", "ls -la /var/lib/$P /var/lib/redis-old"],
}


def collect_artifacts(sandbox, job: str, product: str) -> str:
    cli, conf = f"{product}-cli", f"/etc/{product}/{product}.conf"
    cmds = ARTIFACT_CMDS[job] + ["ps -eo pid,user,args | grep -v ' ps -eo' | head -40", f"tail -n 30 /var/log/{product}/{product}.log",
                                 "pip3 list 2>/dev/null | grep -i -E 'redis|valkey|glide|numpy'"]
    out = []
    for c in cmds:
        c = c.replace("$CLI", cli).replace("$CONF", conf).replace("$P", product)
        try:
            r = sandbox.exec(c, timeout=60)
            out.append(f"$ {c}\n{(r.stdout or '')[-6000:]}{('[stderr] ' + r.stderr[-1500:]) if r.stderr.strip() else ''}")
        except Exception as e:  # noqa: BLE001
            out.append(f"$ {c}\n[artifact error] {e}")
    return "\n\n".join(out)


def grade(sandbox, task) -> tuple[int, str, dict]:
    r = sandbox.exec(task.success.payload, timeout=900)
    summary = {}
    for line in reversed((r.stdout or "").splitlines()):
        if line.startswith("{") and '"pass"' in line:
            try:
                summary = json.loads(line)
            except json.JSONDecodeError:
                pass
            break
    return r.exit_code, ((r.stdout or "") + ("\n[stderr]\n" + r.stderr if r.stderr.strip() else ""))[-20000:], summary


def run_attempt(cfg: dict, tasks: dict, entry: dict, item: dict, fp: dict) -> dict:
    task = tasks[item["task"]]
    adir = wave_dir(cfg) / "attempts" / item["attempt_id"]
    adir.mkdir(parents=True, exist_ok=True)
    rec = {"schema": "cari-kv/record@v1", "wave": cfg["wave"], **item, "model": entry["model"],
           "route": entry["route"], "effort": entry.get("effort"), "started": _utcnow(),
           "config_sha": fp["config_sha"], "tasks_sha": fp["tasks_sha"], "runner_sha": fp["runner_sha"],
           "harness_commit": fp["harness_commit"], "image": task.sandbox.base_image,
           "image_id": fp["images"].get(task.sandbox.base_image)}
    sm = SandboxManager(backend="docker", cpus=cfg["sandbox"]["cpus"], mem=cfg["sandbox"]["mem"])
    sandbox = None
    client = None
    try:
        sandbox = sm.provision(task)
        client = make_client(entry, cfg)
        g = cfg["runaway_guard"]
        runner = KVAgentRunner(client, max_tokens_per_task=g["tokens"], max_tool_calls=g["tool_calls"],
                               wall_clock_s=g["wall_clock_s"])
        doc = DocFetchService(allowlist=cfg["doc_allowlist"])
        att = runner.run(task, doc, sandbox, mode=cfg["mode"])
        usage = list(client.usage_log)
        rec.update({
            "stop_reason": att.stop_reason, "completed": att.completed, "turns": getattr(att, "turns", None),
            "tool_calls": att.tool_calls, "tokens": att.tokens, "tokens_in": att.tokens_in,
            "tokens_out": att.tokens_out, "tokens_reasoning": att.tokens_reasoning,
            "cache_read": sum(u.get("cache_read", 0) + u.get("cached", 0) for u in usage),
            "cache_write": sum(u.get("cache_write", 0) for u in usage),
            "cost_usd": round(sum(u["cost"] for u in usage), 6),
            "cost_sources": sorted({u["cost_source"] for u in usage}),
            "served_models": sorted({str(u.get("model")) for u in usage}),
            "providers": sorted({str(u.get("provider")) for u in usage if u.get("provider")}),
            "wall_seconds": att.wall_seconds, "t0": att.t0, "t1": att.t1, "error": att.error or None,
            "doc_fetches": [getattr(d, "url", str(d)) for d in att.doc_fetches],
            "commands": len(att.commands),
        })
        (adir / "transcript.json").write_text(json.dumps(att.transcript, indent=1, default=str))
        (adir / "commands.json").write_text(json.dumps(att.commands, indent=1))
        (adir / "usage.json").write_text(json.dumps(usage, indent=1, default=str))
        if att.stop_reason == "provider_error":
            rec["verdict"] = "error"  # not a result: refilled on the next run of the lane
        else:
            code, out, summary = grade(sandbox, task)
            rec.update({"verdict": "pass" if code == 0 else "fail", "grader_exit": code,
                        "grader_reasons": summary.get("reasons"), "grader_facts": summary.get("facts")})
            (adir / "grader.txt").write_text(out)
        (adir / "artifacts.txt").write_text(collect_artifacts(sandbox, item["job"], item["product"]))
    except Exception as e:  # noqa: BLE001 - a harness failure is not a model result either
        rec.update({"verdict": "error", "error": f"harness: {type(e).__name__}: {e}"[:800]})
        (adir / "harness_error.txt").write_text(traceback.format_exc())
        if client is not None and getattr(client, "usage_log", None):
            rec["cost_usd"] = round(sum(u["cost"] for u in client.usage_log), 6)
    finally:
        if sandbox is not None:
            try:
                sandbox.teardown()
            except Exception:  # noqa: BLE001
                pass
    rec["finished"] = _utcnow()
    return rec


# ----------------------------------------------------------------------------- subcommands

def cmd_freeze(cfg: dict, _a) -> None:
    fp = fingerprint(cfg)
    missing = [t for t, i in fp["images"].items() if i == "missing"]
    if missing:
        sys.exit(f"images missing: {missing}")
    fp["frozen_at"] = _utcnow()
    p = pathlib.Path(cfg["_path"] + ".frozen")
    if p.exists():
        sys.exit(f"{p} exists - a frozen wave is never re-frozen; make a new wave")
    p.write_text(json.dumps(fp, indent=1) + "\n")
    print(json.dumps(fp, indent=1))


def check_frozen(cfg: dict) -> dict:
    p = pathlib.Path(cfg["_path"] + ".frozen")
    if not p.exists():
        sys.exit("wave is not frozen: run `freeze` first (config is frozen before the first record)")
    frozen = json.loads(p.read_text())
    now = fingerprint(cfg)
    diff = [k for k in ("config_sha", "tasks_sha", "runner_sha", "harness_commit", "images") if frozen.get(k) != now.get(k)]
    if diff:
        sys.exit(f"wave inputs changed since freeze: {diff} - a mid-wave change is a new wave")
    return frozen


def cmd_plan(cfg: dict, _a) -> None:
    items = plan(cfg)
    for it in items:
        print(it["attempt_id"])
    print(f"{len(items)} attempts")


def cmd_run(cfg: dict, a) -> None:
    fp = check_frozen(cfg)
    entry = next((e for e in cfg["park"] if e["key"] == a.lane), None)
    if entry is None:
        sys.exit(f"no lane {a.lane}")
    lock = open(wave_dir(cfg) / f"lane-{a.lane}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"lane {a.lane} is already running")
    tasks = {t.id: t for t in TaskSuiteLoader().load(ROOT / cfg["tasks_dir"])}
    items = [it for it in plan(cfg) if it["lane"] == a.lane]
    streak = 0
    for it in items:
        recs = read_records(cfg)
        done = {r["attempt_id"] for r in recs if r.get("verdict") in ("pass", "fail")}
        if it["attempt_id"] in done:
            continue
        spent = sum(float(r.get("cost_usd") or 0) for r in recs)
        if spent >= float(cfg["budget_stop_usd"]):
            print(f"[{_utcnow()}] BUDGET STOP: ${spent:.2f} spent >= stop line ${cfg['budget_stop_usd']}; lane {a.lane} pauses", flush=True)
            sys.exit(3)
        print(f"[{_utcnow()}] start {it['attempt_id']} (wave spend so far ${spent:.2f})", flush=True)
        rec = run_attempt(cfg, tasks, entry, it, fp)
        append_record(cfg, a.lane, rec)
        print(f"[{_utcnow()}] done  {it['attempt_id']}: {rec.get('verdict')} stop={rec.get('stop_reason')} "
              f"calls={rec.get('tool_calls')} tokens={rec.get('tokens')} ${rec.get('cost_usd')} "
              f"{(rec.get('grader_reasons') or [''])[0][:160] if rec.get('verdict') == 'fail' else rec.get('error') or ''}",
              flush=True)
        streak = streak + 1 if rec.get("verdict") == "error" else 0
        if streak >= 3:
            print(f"[{_utcnow()}] 3 errors in a row - lane {a.lane} stops", flush=True)
            sys.exit(4)
    print(f"[{_utcnow()}] lane {a.lane} complete", flush=True)


def cmd_status(cfg: dict, _a) -> None:
    recs = read_records(cfg)
    latest = {}
    for r in recs:
        latest[r["attempt_id"]] = r
    items = plan(cfg)
    print(f"wave {cfg['wave']}: {sum(1 for i in items if latest.get(i['attempt_id'], {}).get('verdict') in ('pass','fail'))}"
          f"/{len(items)} definitive, spend ${sum(float(r.get('cost_usd') or 0) for r in recs):.2f} (all records incl. errors)")
    for e in cfg["park"]:
        mine = [latest[i["attempt_id"]] for i in items if i["lane"] == e["key"] and i["attempt_id"] in latest]
        v = {k: sum(1 for r in mine if r.get("verdict") == k) for k in ("pass", "fail", "error")}
        print(f"  {e['key']:12} {len(mine):3}/{sum(1 for i in items if i['lane'] == e['key'])}  pass {v['pass']:2} fail {v['fail']:2} "
              f"error {v['error']:2}  ${sum(float(r.get('cost_usd') or 0) for r in recs if r.get('lane') == e['key']):.2f}")
    cells = {}
    for r in latest.values():
        if r.get("verdict") in ("pass", "fail"):
            c = cells.setdefault((r["job"], r["product"]), [0, 0])
            c[0] += r["verdict"] == "pass"
            c[1] += 1
    for (job, prod), (p, n) in sorted(cells.items()):
        print(f"  {job:8} {prod:7} {p}/{n}")


def cmd_ping(cfg: dict, _a) -> None:
    from docka.agent import EXEC_TOOL
    for e in cfg["park"]:
        c = make_client(e, cfg)
        msgs = [{"role": "user", "content": "Use the exec tool to run `echo ping-ok`, then reply with one word: done."}]
        try:
            t1 = c.turn("You are a careful engineer.", [EXEC_TOOL], msgs)
            raw = getattr(t1, "raw_assistant_content", None)
            msgs.append({"role": "assistant", "content": list(raw) if raw is not None else
                         ([{"type": "text", "text": t1.text}] if t1.text else []) +
                         [{"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]} for tc in t1.tool_calls]})
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tc["id"],
                                                      "content": "exit=0\nstdout:\nping-ok\nstderr:\n"} for tc in t1.tool_calls]})
            t2 = c.turn("You are a careful engineer.", [EXEC_TOOL], msgs) if t1.tool_calls else None
            print(json.dumps({"lane": e["key"], "turn1_tool_calls": [tc["input"] for tc in t1.tool_calls],
                              "turn2_text": (t2.text if t2 else None), "usage": c.usage_log}, default=str))
        except Exception as ex:  # noqa: BLE001
            print(json.dumps({"lane": e["key"], "error": f"{type(ex).__name__}: {ex}"[:600]}))


def cmd_proof(cfg: dict, a) -> None:
    tasks = {t.id: t for t in TaskSuiteLoader().load(ROOT / cfg["tasks_dir"])}
    task = tasks[a.task]
    sm = SandboxManager(backend="docker", cpus=cfg["sandbox"]["cpus"], mem=cfg["sandbox"]["mem"])
    sb = sm.provision(task)
    try:
        if a.solution:
            src = pathlib.Path(a.solution).resolve()
            dst = f"/tmp/kv_solution{src.suffix}"
            subprocess.run(["docker", "cp", str(src), f"{sb.container}:{dst}"], check=True)
            run = sb.exec(("python3 " if src.suffix == ".py" else "bash ") + dst, timeout=600)
            print(f"solution exit={run.exit_code}\n{run.stdout[-1500:]}\n{run.stderr[-1500:]}")
        code, out, summary = grade(sb, task)
        verdict = "pass" if code == 0 else "fail"
        ok = verdict == a.expect
        print(json.dumps({"proof": "OK" if ok else "MISMATCH", "task": a.task, "solution": a.solution or "(fixture only)",
                          "expected": a.expect, "got": verdict, "reasons": summary.get("reasons")}, default=str))
        if not ok:
            print(out[-4000:])
            sys.exit(1)
    finally:
        sb.teardown()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ping", "proof", "freeze", "plan", "run", "status"])
    ap.add_argument("--config", default=str(ROOT / "config/wave-1.json"))
    ap.add_argument("--lane")
    ap.add_argument("--task")
    ap.add_argument("--solution")
    ap.add_argument("--expect", choices=["pass", "fail"])
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    {"ping": cmd_ping, "proof": cmd_proof, "freeze": cmd_freeze, "plan": cmd_plan, "run": cmd_run,
     "status": cmd_status}[a.cmd](cfg, a)


if __name__ == "__main__":
    main()
