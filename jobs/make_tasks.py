#!/usr/bin/env python3
"""Generate the six task YAMLs (3 jobs x 2 products) from ONE template per job.

Symmetry is the point: for each job the two prompts differ only in the product's name, version
and config path, and the graders differ only in which product/version they expect. The graders are
inlined into `success.payload`, so they live in the task file and never inside the image the
agent works in.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "tasks"
OUT2 = ROOT / "tasks-wave2"  # wave-2 has its own task dir: wave-1 hashes every YAML in tasks/

PRODUCTS = {
    "valkey": {"name": "Valkey", "ver": "9.1", "full": "9.1.2", "conf": "/etc/valkey/valkey.conf",
               "cli": "valkey-cli", "app_image": "cari-kv-app:valkey-9.1", "mig_image": "cari-kv-mig:valkey-9.1"},
    "redis": {"name": "Redis", "ver": "8.10", "full": "8.10.2", "conf": "/etc/redis/redis.conf",
              "cli": "redis-cli", "app_image": "cari-kv-app:redis-8.10", "mig_image": "cari-kv-mig:redis-8.10"},
}

PROMPTS = {
    "cache": """\
The storefront code in /app reads products through `catalog.get_product()` in `/app/catalog.py`. Every call goes to the inventory service at http://127.0.0.1:8000, which takes about 300 ms per request, and the storefront makes thousands of these calls a minute from many processes.

{name} {ver} is installed and running on this machine at 127.0.0.1:6379 (config file: {conf}).

Add caching to `/app/catalog.py` using that {name} server:
- a product that was read recently must be served from {name}, so the cache is shared by every storefront process, instead of calling the inventory service again;
- cached products expire after 5 minutes;
- after `catalog.update_price()` changes a price, the next `catalog.get_product()` call for that product must return the new price, in any process.

Keep the names, arguments and return values of `get_product()` and `update_price()` unchanged. Do not modify the inventory service.
""",
    "vector": """\
`/data/articles.jsonl` holds 1,000 knowledge-base articles, one JSON object per line with the fields `id`, `title` and `embedding` (a list of 64 floats).

{name} {ver} is installed and running on this machine at 127.0.0.1:6379 (config file: {conf}).

Build semantic search over these articles in {name}:
1. Load all 1,000 articles into {name}.
2. Write `/app/search.py`. We will run it as `python3 /app/search.py < query.json`, where query.json contains one query embedding (a JSON list of 64 floats). It must print the `id`s of the 5 articles most similar to the query by cosine similarity, one per line, most similar first.

The similarity search must run inside {name}: `search.py` must not read `/data/articles.jsonl` and must not compute similarities itself. Leave the articles and everything `search.py` needs in {name} when you finish.
""",
    "migrate8": """\
This machine runs two servers:
- the current server: Redis 8.10 at 127.0.0.1:6379 (config file: /etc/redis-old/redis.conf, binaries in /opt/redis-8.10/bin). It holds our production data.
- the new server: {name} {ver} at 127.0.0.1:6380 (config file: {conf}). It is empty.

We are moving from Redis 8.10 to {name} {ver}. Copy all data from the current server to the new one, so that the new server holds an exact copy: every key in every database, with the same type, the same value and the same remaining time to live, including stream consumer groups.

Applications will be switched to the new server right after you finish, so it must then run as a standalone primary that accepts writes. Leave the current server running, and do not change its data.
""",
    "migrate": """\
This machine runs two servers:
- the current server: Redis 7.2 at 127.0.0.1:6379 (config file: /etc/redis-old/redis.conf, binaries in /opt/redis-7.2/bin). It holds our production data.
- the new server: {name} {ver} at 127.0.0.1:6380 (config file: {conf}). It is empty.

We are moving from Redis 7.2 to {name} {ver}. Copy all data from the current server to the new one, so that the new server holds an exact copy: every key in every database, with the same type, the same value and the same remaining time to live, including stream consumer groups.

Applications will be switched to the new server right after you finish, so it must then run as a standalone primary that accepts writes. Leave the current server running, and do not change its data.
""",
}


class _Lit(str):
    pass


def _lit(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_Lit, _lit, Dumper=yaml.SafeDumper)


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(grader: str, cfg: dict, prelude: str = "") -> str:
    code = (prelude + "\n" if prelude else "") + f"CFG = {json.dumps(cfg, sort_keys=True)}\n\n" + grader
    assert "KVGRADER_EOF" not in code
    return f"/opt/kv-grader/bin/python - <<'KVGRADER_EOF'\n{code}\nKVGRADER_EOF\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    g = ROOT / "graders"
    expected = json.loads((g / "cache_expected.json").read_text())
    vq = json.loads((g / "vector_queries.json").read_text())
    up = ROOT / "images/assets/upstream"
    kvdump = (ROOT / "images/assets/mig/kvdump.py").read_text()
    written = []
    for job, prods in (("cache", PRODUCTS), ("vector", PRODUCTS), ("migrate", PRODUCTS), ("migrate8", {"valkey": PRODUCTS["valkey"]})):
        for prod, P in prods.items():
            tid = f"{job}_{prod}"
            if job == "cache":
                cfg = {"product": prod, "version": P["full"], "cli": P["cli"],
                       "expected": {"7": expected["7"], "12": expected["12"]},
                       "upstream_sha256": {"/opt/upstream/server.py": sha(up / "server.py"),
                                           "/opt/upstream/products.json": sha(up / "products.json")}}
                pl = payload((g / "grade_cache.py").read_text(), cfg)
                image, mem = P["app_image"], "1g"
                pre = [f"{P['name']} {P['full']} on 127.0.0.1:6379, empty", "inventory service on 127.0.0.1:8000 (300 ms per GET)",
                       "/app/catalog.py calls the inventory service on every read"]
            elif job == "vector":
                cfg = {"product": prod, "version": P["full"], "cli": P["cli"],
                       "queries": [q["embedding"] for q in vq], "top5": [q["top5"] for q in vq]}
                pl = payload((g / "grade_vector.py").read_text(), cfg)
                image, mem = P["app_image"], "1g"
                pre = [f"{P['name']} {P['full']} on 127.0.0.1:6379 with the image's bundled modules loaded, empty",
                       "/data/articles.jsonl: 1,000 articles with 64-d embeddings"]
            elif job == "migrate8":
                cfg = {"product": prod, "version": P["full"], "old_version": "8.10.2",
                       "old_digest_path": "/var/lib/kv-seed/old.digest"}
                pl = payload((g / "grade_migrate8.py").read_text(), cfg, prelude=(ROOT / "images/assets/mig8/kvdump2.py").read_text())
                image, mem = "cari-kv-mig8:valkey-9.1", "1g"
                pre = ["Redis 8.10.2 on 127.0.0.1:6379 seeded with 3,310 keys in db0 and db2 (the wave-1 dataset plus 40 hashes with per-field expiry)",
                       f"{P['name']} {P['full']} on 127.0.0.1:6380, empty"]
            else:
                cfg = {"product": prod, "version": P["full"], "old_version": "7.2.16",
                       "old_digest_path": "/var/lib/kv-seed/old.digest"}
                pl = payload((g / "grade_migrate.py").read_text(), cfg, prelude=kvdump)
                image, mem = P["mig_image"], "1g"
                pre = ["Redis 7.2.16 on 127.0.0.1:6379 seeded with 3,270 keys in db0 and db2 (all types, TTLs, streams with groups)",
                       f"{P['name']} {P['full']} on 127.0.0.1:6380, empty"]
            task = {
                "id": tid,
                "difficulty": "medium",
                "surface": prod,
                "in_subset": True,
                "task_version": 1,
                "prompt": _Lit(PROMPTS[job].format(**P)),
                "sandbox": {
                    "base_image": image,
                    "preconditions": pre,
                    "fixtures": [],
                    "network": "bridge",
                    "entrypoint_mode": "image",
                    "ready_cmd": "test -f /run/kv-ready",
                    "memory": mem,
                },
                "success": {"kind": "shell_command", "payload": _Lit(pl)},
            }
            path = (OUT2 if job == "migrate8" else OUT) / f"{tid}.yaml"
            path.parent.mkdir(exist_ok=True)
            path.write_text(yaml.safe_dump(task, sort_keys=False, allow_unicode=True, width=1000))
            written.append(path.name)
    print("wrote", written)


if __name__ == "__main__":
    main()
