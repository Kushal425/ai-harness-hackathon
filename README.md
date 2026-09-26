# Raven

A self-improving, conversational coding-agent harness, built for the **LCC × DevClub AI Coding Harness Hackathon 2026**.

Raven turns a foundation language model into an autonomous software engineer: it understands a task, plans the work, navigates the repository with purpose-built tools, makes reversible edits, proves its changes with evidence (not claims), recovers from failures on its own, and talks with you throughout.

## Status

All three build phases are implemented:

- **Phase 1** — the core pipeline: CLI, LLM gateway, tool layer + policy, repo digest, a text action-protocol executor, task understanding + planner, and a verifier core that scores evidence rather than trusting the model's word.
- **Phase 2** — robustness and interaction: a recovery system (stall detection, repeated-action nudges, a capped Debugger sub-agent), a TUI with real approval prompts and a hard fallback to a plain REPL, execution tracing + Ochiai fault localization + a behavioral-diff check, and Explorer/Reviewer sub-agents behind a `delegated` executor strategy.
- **Phase 3** — self-improvement: prompts extracted to versioned YAML, an in-run reflection + cross-task lesson loop with persistent project memory, and an offline prompt-evolution/config-tuning harness (see [Learning & evolution](#learning--evolution) for what's genuinely validated vs. what needs a live model).

## Quick start

```bash
make setup
export AI_API_KEY="<your-api-key>"
make run
```

In a real terminal this launches the TUI (rich panels, streaming, plan/activity view). Piped input or `TERM=dumb` falls back to a plain REPL automatically — the harness never requires a fancy terminal to work.

`make test` runs the full offline test suite (205 tests, no API key or network — everything is exercised against a scripted `FakeClient`).

```bash
make test               # offline unit + integration tests
make eval                # eval harness against the toy_repo fixture — see Results below
```

## Supplying an issue

Four ways in, matching plan §14's autonomous evaluation path:

```bash
# 1. Flag
raven --repo /path/to/repo --issue "average() returns the wrong value for [2,4,6]"

# 2. File
raven --repo /path/to/repo --issue-file issue.txt

# 3. Piped stdin (non-interactive — prints a delimited result block and exits)
echo "fix: off-by-one in average()" | make run

# 4. Pasted into the TUI/REPL chat — issue-like text (length, stack traces,
#    "steps to reproduce", a GitHub issue URL) is auto-detected and switches
#    the session straight into autonomous mode.
```

`--strategy` overrides `config.yaml`'s `executor.strategy` (`single_loop` | `plan_execute` | `delegated`) for one run.

## Conversation layer

Modes: **Chat** (default, read-only, uses tools to answer questions about the repo) · **Plan** (`/plan <task>`, shows a plan and waits) · **Act** (`/act`, executes the exact plan you were shown — it does not silently recompute one) · **Autonomous** (`/auto <task>`, or auto-detected) · **Review** (`/review`, an independent critique of the current diff).

Slash commands: `/help /plan /act /auto /review /diff /undo /checkpoints /evidence /memory /lessons /budget /config /model /repo /chat /clear /exit`. These are implemented once in `raven/session/manager.py` and shared identically by both the TUI and the plain REPL — there is no behavioral difference between them beyond rendering.

## Architecture

```
raven/
├── cli.py · config.py · prompts.py           entry point, config loading, versioned prompt loader
├── llm/            gateway.py · providers.py · fake.py · protocol.py
├── ui/              tui.py (rich + prompt_toolkit) · repl.py (plain fallback)
├── session/         manager.py                mode/slash-command logic, shared by both UIs
├── core/            understand.py · planner.py · executor.py · judge.py · orchestrator.py · protocol.py
├── tools/           registry.py · policy.py · fs.py · search.py · symbols.py · edit.py · shell.py
│                    test_runner.py · git_tool.py
├── repo/            digest.py                 file tree + symbol index + test-command detection
├── context/         engine.py · decay.py       fresh-assembled context, age-based decay
├── recovery/        checkpoints.py · handlers.py
├── agents/          base.py · explorer.py · reviewer.py · debugger.py
├── verify/          baseline.py · reproduce.py · score.py · trace.py · sbfl.py · behavior_diff.py
├── memory/          lessons.py · project.py     cross-task lessons, durable project facts
├── learn/           reflect.py · extract_lessons.py · evolve.py · tune.py
└── report/          report.py

prompts/base.yaml    every prompt the harness sends to a model, versioned (plan §12.3)
evals/                tasks/*.yaml · run_evals.py · results/baseline.md
tests/                 205 offline tests (FakeClient) + tests/fixtures/toy_repo
```

**Orchestrator state machine** (`raven/core/orchestrator.py`):

```
single_loop:   INTAKE -> DIGEST -> REPRODUCE+EXECUTE -> VERIFY -> JUDGE -> FINALIZE
plan_execute:  INTAKE -> UNDERSTAND -> DIGEST -> PLAN -> EXECUTE(per step)
               -> VERIFY -> JUDGE -> (REPLAN -> PLAN)* -> FINALIZE
delegated:     same as plan_execute, plus Explorer delegation on exploratory
               steps and one Reviewer pass on the final diff
```

**Reproduce first, verified by the harness** (`raven/verify/reproduce.py`, plan §11.2). For bug fixes and features in a pytest repo, the executor first writes a reproduction test at `.raven/repro/test_repro.py` that must fail on the current code, then fixes the code until it passes. Raven then checks that claim itself, independently of the model: it swaps the original files back in, runs the reproduction (it must **fail**), restores the fix, and runs it again (it must **pass**). This is what makes a fix provable when the repo has no failing test for the issue, e.g. when the grader's tests are hidden. A reproduction that still fails on the fix gets the run rejected; one that never failed earns no credit. The test lives in Raven's git-excluded scratch dir, is archived next to the run's `report.md`, and never appears in the patch. Toggle: `verify.reproduce` in `config.yaml`.

Every run — regardless of strategy — also runs the learning loop: pinned cross-task lessons are retrieved before EXECUTE, in-run reflection fires on failed steps (capped at 3), and a new lesson is extracted and stored at the end.

## Sample run report

A real report from the eval harness (`evals/work/bug_average_single_loop/.raven/runs/*/report.md`), lightly trimmed:

```markdown
# Raven run 20260926T203639-4f9236

**Goal:** Fix the failing test_average test in tests/test_arithmetic.py. The
average() function in calc/arithmetic.py has an off-by-one bug.

**Outcome:** RESOLVED
**Reason:** evidence score 1.0 meets threshold; no new test failures

## Evidence
{
  "evidence_score": 1.0,
  "repro_fixed": true,
  "no_new_failures": true,
  "pre_failed": ["tests/test_arithmetic.py::test_average", "tests/test_strings.py::test_is_palindrome_false"],
  "post_failed": ["tests/test_strings.py::test_is_palindrome_false"],
  "collateral_changes": []
}

## Execution story
outcome: AssertionError:
-> test_average() at line 12
-> average() at line 9
exception raised at line 13

## Suspicious locations (Ochiai)
- 1.0    calc/__init__.py:0
- 0.707  calc/arithmetic.py:1
- 0.707  calc/arithmetic.py:11
- 0.707  calc/arithmetic.py:5
- 0.707  calc/arithmetic.py:9

## Files changed
- calc/arithmetic.py

## Usage
- tool calls: 4  ·  LLM calls: 6  ·  tokens: 4041
```

## Eval results

`evals/results/baseline.md`, produced by `python evals/run_evals.py` against `tests/fixtures/toy_repo` (a small package with 2 planted bugs) using a `FakeClient` scripted with the correct action sequence per task — this makes the number reproducible offline, but it measures "does the pipeline work end to end," not model quality (see below):

**Resolved: 14/14 · avg tokens: 3405 · avg tool calls: 2.4**, split across `single_loop` (8 tasks), `plan_execute` (3), and `delegated` (3) strategies, and task types bug_fix/feature/refactor/test_writing/question. `t14` is graded the way the hackathon likely grades: an issue-style goal with no test named, scored by a **hidden test** the agent never sees (written in only after the run), and it additionally requires the reproduction to be verified fail→pass.

## Learning & evolution

Being precise about what's real here, because it's easy to overstate:

**Genuinely working and tested:**
- Prompts live in `prompts/base.yaml`, versioned, loaded once via `raven/prompts.py` — no prompt text is hardcoded in Python.
- In-run reflection: a failed step triggers one model call ("what did you assume that was wrong?"), capped at 3 per run, pinned into context for the rest of that run.
- Cross-task lessons: every run ends with a lesson-extraction call; lessons are deduplicated by word-overlap, stored per-repo (`.raven/memory/lessons.jsonl`) or globally (`memory/global_lessons.jsonl`), and retrieved (deterministically, no model call) at the start of future runs. **Verified end to end**: `tests/test_learning.py::test_second_run_context_contains_lesson_extracted_from_first_run` proves a lesson from one run is pinned into a second, independent run's context.
- Project memory: durable facts (e.g. the detected test command) are appended to `.raven/memory/project.md`, idempotently.
- `/lessons` shows what's been learned about the current repo.

**Mechanism-verified, not quality-verified — needs a live model:**
- `raven/learn/evolve.py` (`make evolve`) implements the full reflective-evolution loop from plan §12.3: population, Pareto selection on (resolve rate, tokens), a real model call to diagnose failures and rewrite a prompt module, crossover, and a held-out gate that requires at least one more resolved task before promoting anything to `prompts.tuned.yaml`. Run offline (no `AI_API_KEY`), this correctly **never promotes a candidate** — the eval harness's tasks are scripted with a fixed, hardcoded tool-call sequence per task, so their outcome cannot change based on prompt text. That's not a bug; it's the held-out gate doing its job. A genuine tuning signal requires a live model and real budget.
- `raven/learn/tune.py` (`make evolve` doesn't cover this, run directly: `python -m raven.learn.tune`) is the same story for numeric config knobs (successive halving over `half_life`/`max_replans`). It also surfaces a real, separate gap: `config.yaml`'s `context.half_life` / `recovery.stall_turns` etc. are declared but not currently read by `run_orchestrator` at call time — its Python-level defaults are used unless a caller passes explicit kwargs. `tune.py`'s sweep operates on those kwargs directly; wiring config.yaml through end-to-end is un-fixed and noted rather than silently glossed over.
- The `delegated` and `plan_execute` executor strategies are both implemented and covered by tests/evals, but neither is the `config.yaml` default over `single_loop` — promoting either needs a real ablation against the prescribed model, which FakeClient-scripted evals cannot provide (see the `# PLAN-DECISION` comments in `raven/core/orchestrator.py`).

## Security

- `AI_API_KEY` is read only from the environment — never from config or code.
- Subprocess environments are scrubbed of anything matching `KEY`/`TOKEN`/`SECRET` before a shell tool runs.
- File operations are jailed to the target repository; `create` never overwrites an existing file.
- Editing an *existing* test file is denied in every mode (creating new test files — for test-writing/feature tasks — is allowed).
- No network access, no `git push`, no history rewrites, no `rm -rf` — the shell tool only allows a fixed command allowlist, enforced in `raven/tools/policy.py`.
- Every write is checkpointed before it happens; a failed or aborted run restores the tree exactly, verified by tests including a deliberately-wrong-edit chaos case.

## Limitations

- No live model was available in this development environment — every number above comes from `FakeClient`-scripted runs. The pipeline's *mechanics* are real and tested; its *quality against a real model* is unvalidated until run with one.
- The eval fixture (`tests/fixtures/toy_repo`) is small and Python-only; language coverage for JS/TS/Go/Java/Rust in `raven/tools/symbols.py` and `raven/repo/digest.py` is minimal (extension-based language guess only).
- `config.yaml`'s numeric knobs aren't fully wired through to the orchestrator (see Learning & evolution above).
- Native tool-calling protocol is not implemented — only the text fenced-action-block protocol exists. `llm.tool_protocol: auto` in config is aspirational until a native path is built.
- `delegated`/`plan_execute` are not the shipped default; see above.

## Submission checklist (plan §22)

- [x] `make setup && make run` works from a clean checkout; `make test` works offline without a key (verified with a real `git clone` of this repo into `/tmp`, from scratch, no `.venv`/`.raven` carried over — 167/167 tests, piped autonomous run both passed)
- [x] `AI_API_KEY` from environment only; `.env.example` has an empty value; no secrets in the repo
- [x] Model/endpoint defined in config, overridable by env (`RAVEN_MODEL`, `RAVEN_BASE_URL`)
- [x] Seed, temperature, frozen base config documented (`config.yaml`); `config.tuned.yaml`/`prompts.tuned.yaml` are supported override paths, not present by default (no genuine tuning run has been done — see above)
- [x] Issue input works via flag, file, stdin, and TUI/REPL paste
- [x] Autonomous run leaves only the intended patch; report written; result block printed
- [x] Integrity guarantees (no network/push/history-rewrite, env scrubbing, path jailing) enforced in code
- [x] README has quick start, architecture, sample report, eval results, and limitations
- [ ] **Not applicable yet**: "prescribed model" / "organiser endpoint" — this hackathon detail isn't available in this environment; `RAVEN_MODEL`/`RAVEN_BASE_URL` make swapping to it a two-env-var change
- [ ] Real eval/evolution curve against a live model — pending `AI_API_KEY` and real budget
