#!/usr/bin/env python3
"""Generate the deterministic data for CARI Issue 2 (Valkey vs Redis), execution wave.

Writes, from one fixed seed, everything the images and graders need:
  images/assets/upstream/products.json   50 products for the cache job's inventory service
  images/assets/data/articles.jsonl      1,000 knowledge-base articles with 64-d embeddings
  graders/vector_queries.json            5 held-out queries + their exact cosine top-5 (GRADER ONLY,
                                         never copied into an image: the agent must not see them)
  graders/cache_expected.json            the original records the cache grader compares against

Queries are accepted only with a clear margin (top-1 vs top-2 >= 0.10, 5th vs 6th >= 0.02), so an
approximate (HNSW) index and float32 rounding cannot turn a correct search into a wrong answer.
Pure standard library on purpose: the same output on the laptop and on the box.
"""
from __future__ import annotations

import json
import math
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent
SEED = 20261002
DIM = 64
N_ARTICLES = 1000

TOPICS = [
    "Reset a forgotten password", "Set up single sign-on", "Export invoices to CSV",
    "Change the billing plan", "Invite a teammate", "Remove a user from the workspace",
    "Connect the Slack integration", "Rotate an API key", "Configure webhooks",
    "Restore a deleted project", "Enable two-factor authentication", "Import contacts from a spreadsheet",
    "Set notification preferences", "Create a custom report", "Merge duplicate records",
    "Schedule a recurring task", "Change the workspace timezone", "Download an audit log",
    "Fix a failed payment", "Close the account",
]
VARIANTS = [
    "on the web app", "on iOS", "on Android", "for administrators", "for new users",
    "step by step", "troubleshooting", "common errors", "for enterprise plans", "quick guide",
]
ADJ = ["Classic", "Compact", "Deluxe", "Eco", "Everyday", "Heavy-duty", "Mini", "Pro", "Smart", "Travel"]
NOUN = ["backpack", "blender", "desk lamp", "kettle", "monitor stand", "notebook", "office chair",
        "speaker", "water bottle", "headphones"]


def unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # both unit-length


def main() -> None:
    rng = random.Random(SEED)

    # ---------------- products (cache job)
    products = []
    for i in range(1, 51):
        products.append({
            "id": i,
            "name": f"{ADJ[(i * 7) % len(ADJ)]} {NOUN[(i * 3) % len(NOUN)]}",
            "price": round(rng.uniform(5, 500), 2),
            "currency": "USD",
        })
    up = ROOT / "images/assets/upstream"
    up.mkdir(parents=True, exist_ok=True)
    (up / "products.json").write_text(json.dumps(products, indent=1) + "\n")

    # ---------------- articles (vector job)
    centroids = [unit([rng.gauss(0, 1) for _ in range(DIM)]) for _ in TOPICS]
    articles = []
    for i in range(N_ARTICLES):
        k = i % len(TOPICS)
        v = unit([c + rng.gauss(0, 0.12) for c in centroids[k]])
        v = [round(x, 6) for x in v]  # what the file carries is what the server sees
        articles.append({
            "id": f"kb-{i + 1:04d}",
            "title": f"{TOPICS[k]} - {VARIANTS[(i // len(TOPICS)) % len(VARIANTS)]} ({i // (len(TOPICS) * len(VARIANTS)) + 1})",
            "embedding": v,
        })
    data = ROOT / "images/assets/data"
    data.mkdir(parents=True, exist_ok=True)
    with (data / "articles.jsonl").open("w") as f:
        for a in articles:
            f.write(json.dumps(a, separators=(",", ":")) + "\n")

    # unit vectors from the ROUNDED values, i.e. exactly what a loader stores
    vecs = [unit(a["embedding"]) for a in articles]
    queries = []
    while len(queries) < 5:
        anchor = rng.randrange(N_ARTICLES)
        q = unit([x + rng.gauss(0, 0.05) for x in vecs[anchor]])
        q = [round(x, 6) for x in q]
        qu = unit(q)
        scored = sorted(((cos(qu, v), articles[j]["id"]) for j, v in enumerate(vecs)), reverse=True)
        top = scored[:6]
        if top[0][0] - top[1][0] < 0.10 or top[4][0] - top[5][0] < 0.02:
            continue  # margin too thin to grade an approximate index fairly
        if any(qq["top5"][0] == top[0][1] for qq in queries):
            continue
        queries.append({
            "embedding": q,
            "top5": [s[1] for s in top[:5]],
            "scores": [round(s[0], 6) for s in top[:6]],
        })

    graders = ROOT / "graders"
    graders.mkdir(parents=True, exist_ok=True)
    (graders / "vector_queries.json").write_text(json.dumps(queries) + "\n")
    (graders / "cache_expected.json").write_text(json.dumps({str(p["id"]): p for p in products}) + "\n")

    print(f"products: {len(products)}  articles: {len(articles)}  queries: {len(queries)}")
    for q in queries:
        print("  query top5", q["top5"], "scores", q["scores"])


if __name__ == "__main__":
    main()
