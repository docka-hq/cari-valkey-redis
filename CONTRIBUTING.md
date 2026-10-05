# Contributing

Two kinds of contribution are useful: a **selection prompt** (what do agents pick when nobody names a product) and a **job** (can an agent finish this on the server it is given). Open an issue or a pull request with either.

## A selection prompt

Add an entry in the format of `selection/config.json`:

```json
{"id": "node_job_queue", "turf": "job queues for Node.js", "prompt": "I need a background job queue for my Node.js app: send emails, resize images, retry jobs that fail. What do you set up?"}
```

- The prompt must not name the products being compared, or anything that implies one of them.
- Write it the way a developer, or an agent, would actually ask.
- Say in `turf` what it tests.
- A variant of an existing prompt (another language, the same need asked by a person and by an agent) is the most useful kind: it isolates one difference.

Answers are coded by the rules in `selection/README.md`. If a prompt needs a different rule, say which and why.

## A job

A job is one YAML file, in the format described in `jobs/README.md`, plus what it needs to run:

1. **The prompt**, the same for every product except the name, version and config path.
2. **A container image** with the starting state (a Dockerfile and assets under `images/`).
3. **A grader** that runs inside the container after the agent stops, checks the end state, prints one JSON line `{"pass": ..., "reasons": [...], "facts": {...}}` and exits 0 on a pass.
4. **Proofs**: one correct solution that passes, and at least one known wrong solution that fails (see `jobs/reference/`). A grader we cannot prove both ways is not used.

The grader checks what the prompt asks for, nothing more, and never trusts what the agent says it did. No job may need a secret or an outside account.

## What happens next

We check the proofs, freeze the change into the next wave, run it on the same models with the same protocol, and publish the result, failures included. If a vendor changes something these jobs depend on, we can rerun the same frozen jobs and publish before and after.
