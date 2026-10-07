# Can agents run Valkey?

Issue 2 of **Can Agents Run It?**, a series by [Docka](https://docka.ai) that measures whether AI agents can finish real jobs in software products.

- Results page: https://lab.docka.ai/can-agents-run-it/valkey/
- Explorer, every answer and attempt with its own link: https://lab.docka.ai/can-agents-run-it/valkey/explore/
- **Run these tests yourself, on any model, or add your own: the test kit, https://github.com/docka-hq/can-agents-run-valkey.** This repository is the frozen record of the published runs.
- **Outside review, 2026-10-06:** what it found, what the re-check of the published runs shows (no number changes) and what changed: [REVIEW-2026-10-06.md](REVIEW-2026-10-06.md).

This repository holds everything behind those pages: the prompts, the job definitions and their graders, the container images, every model answer, every agent attempt, and the code that ran and counted them. Configuration, prompts, graders and images were frozen and hashed before the first record of each wave. Nothing here was edited after the runs.

## Results

| Question | Result |
|---|---|
| A person asks for a cache, a job queue or a semantic cache (two of the three prompts name Python) | Valkey 0 of 75. Redis 60, something else 15. |
| A managed in-memory store on AWS | Valkey 16 of 25 |
| An agent that provisions and runs the cache itself | Valkey 19 of 25 |
| Told which server to use: a cache, semantic search, a migration from Redis 7.2 | Valkey 45 of 45, Redis 45 of 45 |
| A migration from Redis 8.10 to Valkey 9.1 | 11 of 15 exact copies |

Five models: Claude Opus 5.5, GPT-6 Sol, DeepSeek V4.1 Flash, GLM-5.3-Flash, MiMo-V2.6-Flash. Valkey 9.1.2 and Redis 8.10.2, each with its own search module.

The five selection prompts differ in more than who is asking, so they show where Valkey wins, not yet why. Counts are small: read them as counts, not percentages.

## What is where

| Path | Contents |
|---|---|
| `selection/config.json` | The five prompts, the models, the protocol. `config.frozen.json` holds its hash. |
| `selection/answers/` | Every answer as returned by the model, with tokens and cost. `refill.jsonl` is the one answer asked again after it came back empty. |
| `selection/coded/` | How each answer was counted, and the totals. |
| `selection/hand-review.json` | The 8 answers coded by hand, each with its reason. |
| `selection/kv_classify.py` | The coding rules as code. See `selection/README.md`. |
| `jobs/wave-1/`, `jobs/wave-2/` | One YAML file per job: the prompt, the container, the starting state and the grader. See `jobs/README.md`. |
| `jobs/graders/` | Grader sources. `jobs/make_tasks.py` inlines them into the YAML files. |
| `jobs/reference/` | One correct solution per job and the known wrong ones used to prove each grader. |
| `jobs/system_prompt.txt` | The system prompt every model got on every job. |
| `jobs/config/` | Wave configs and their frozen hashes. |
| `images/` | Dockerfiles and assets for the job containers. `jobs/gen_assets.py` generates the data. |
| `runs/wave-1/`, `runs/wave-2/` | Every attempt: `transcript.json` (the model's turns and tool calls), `commands.json`, `artifacts.txt` (the code and state it left, read after it stopped), `grader.txt` (the verdict), `usage.json`. Wave 2 also stores every tool output in `tool_log.json`. `records-*.jsonl` are the run records; `aggregate/` is derived from them. |
| `runners/` | The scripts that ran the waves. They need Docka's internal harness, which is not part of this repository. |

## How it was run

**Selection.** Each prompt went to each model five times, as the only message, with no system prompt, at the provider's default settings. Answers were coded by what they actually set up: first the code (a container image, a Terraform engine, an install line; comments and fallback tips ignored), then a named managed service, then the line that states the choice. A client library never counts, because the redis Python client talks to Valkey too.

**Jobs.** Each attempt starts in a fresh container from the frozen image, with the server running, both Python clients installed (redis 8.1.0, valkey 6.1.1), open internet, a shell and a documentation fetcher. The Valkey and Redis prompts differ only in the product's name, version and config path. When the agent stops, a grader checks the server, not the agent's account of it. Every grader was proven before the runs: it fails on the untouched environment and on known wrong solutions, and passes on a reference solution, for both products (22 of 22 checks in wave 1, 4 of 4 in wave 2). Three attempts per model, job and product. No caps on steps or tokens; a runaway guard (3M tokens, 500 tool calls, 60 minutes) is recorded if it fires.

Spend for the whole study: $8.58. Four models are metered by OpenRouter; Claude is usage at Anthropic list price.

## Limits

- Five models, one point in time.
- Bare prompts, not a coding tool inside a repository. A repository that already uses Redis would likely push models further toward Redis.
- Two selection prompts name Python, and the prompts differ in more than who is asking.
- Three attempts per cell, five answers per prompt and model.

## Contributing

Think a prompt is unfair, or want a job tested that matters to you? Propose it in the [test kit](https://github.com/docka-hq/can-agents-run-valkey), which also runs it. This repository stays as the record of the published runs; [CONTRIBUTING.md](CONTRIBUTING.md) describes the formats.

## Licence

Code (`*.py`, `*.sh`, Dockerfiles): MIT, see [LICENSE](LICENSE). Data (prompts, answers, transcripts, records): CC BY 4.0, see [LICENSE-DATA.md](LICENSE-DATA.md). Model outputs are included as recorded.

Questions: eugene@docka.ai
