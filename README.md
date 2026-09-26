# Raven

A self-improving, conversational coding-agent harness, built for the **LCC × DevClub AI Coding Harness Hackathon 2026**.

Raven turns a foundation language model into an autonomous software engineer: it understands a task, plans the work, navigates the repository with purpose-built tools, makes reversible edits, proves its changes with evidence (not claims), recovers from failures on its own, and talks with you throughout.

## Status

**Phase 1 (in progress):** the core end-to-end pipeline — CLI, LLM gateway, plain REPL. This is being built incrementally; see `PLAN.md`-equivalent context in the project history for the full architecture and 3-phase build plan.

## Quick start

```bash
make setup
export AI_API_KEY="<your-api-key>"
make run
```

`make test` runs the offline unit test suite (no API key, no network required — it uses a `FakeClient`).

## Configuration

Runtime config lives in `config.yaml`. Nothing secret lives in config or code — only `AI_API_KEY` is read from the environment. Override the model/endpoint with:

```bash
export RAVEN_MODEL="<model-name>"
export RAVEN_BASE_URL="<endpoint>"
```

## Repository layout

```
raven/
├── __main__.py · cli.py · config.py
├── llm/            gateway.py · providers.py · fake.py · protocol.py
├── ui/             repl.py
tests/              offline unit tests (FakeClient)
config.yaml         default configuration
.env.example        documents AI_API_KEY (left empty)
```

This will grow across Phase 1–3 into the full architecture: task understanding, planner, executor, tool layer, context engine, memory, recovery system, verifier (evidence-first proof of changes), sub-agents, and a learning loop with offline prompt evolution.

## Security

- The API key is read only from `AI_API_KEY`.
- Subprocess environments are scrubbed of secret-like variables before any shell tool runs.
- File operations are jailed to the target repository and a scratch directory.
- No network access, no `git push`, no history rewrites — enforced in code.
