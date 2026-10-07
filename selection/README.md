# Selection: prompts and coding rules

`config.json` holds the five prompts (`scenarios`: `id`, `turf`, `prompt`), the five models (`park`) and the protocol: a bare user prompt, no system prompt, one turn, provider-default sampling, five answers per model and prompt.

## How an answer is counted

`kv_classify.py` reads each answer and records what it **sets up**, in this order:

1. **Code first**: a container image (`image: valkey/valkey`, `docker run redis`), a Terraform engine or variable default, an install line, a Helm chart. Comments and fallback tips are ignored.
2. **Then a named managed service** (for example ElastiCache for Valkey).
3. **Then the line that states the choice** ("I'd use Redis"), read in context.
4. A client library never counts: the redis Python client talks to Valkey too.

When the evidence conflicts (for example a semantic cache built on SQLite that names Redis as the way to scale), the answer is coded by hand. The hand decisions are in `hand-review.json`, each with its reason: 8 made before publication, and 1 correction made on 2026-10-06 after an outside review (see `REVIEW-2026-10-06.md`). `coded/classified.jsonl` has one row per answer: the code (`primary`), what it was based on (`basis`), and whether the answer mentions Valkey or Redis or gives a license as a reason.

One answer came back empty because the model spent its whole output budget thinking. It was asked again with a larger budget (`answers/refill.jsonl`, `kv_refill.py`). An empty answer is never coded.
