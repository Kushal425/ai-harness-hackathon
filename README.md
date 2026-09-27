# Raven

An autonomous coding-agent harness built for the **LCC × DevClub AI Coding Harness Hackathon 2026**, designed around one idea:

> **Don't ask the model whether a patch is right. Generate a few candidate fixes, run them against each other, and spend the model's judgement only where they disagree — on the single input that separates them, as a concrete multiple-choice question backed by evidence from the repository.**

We call it **Crux**. `make demo` shows it in 30 seconds, offline:

```
◆ probe    reproduced: chunk([1, 2, 3, 4, 5], 2) -> => [[1, 2], [3, 4]]
   ✓ c1 alive (2 changed lines) — stop the range at len(items)
   ✓ c2 alive (3 changed lines) — ceiling-divide to count chunks
   ✓ c3 alive (5 changed lines) — loop to the end
◆ cluster  3 surviving candidates -> 2 behaviour cluster(s) over 13 inputs
✦ crux     chunk([], 2)   [c1, c3] => []  vs  [c2] => [[]]
⚖ verdict  A: window() documents that empty input yields no windows; tests assert window([], 2) == []
★ select   selected c1 (2 changed lines)

hidden grader test (never shown to Raven):
  selected c1: PASS
  rejected c2: FAIL  (passed every visible test — only the crux caught it)
```

## Why this design

Every choice below comes from a measured result, not a hunch:

| Finding | Source | What Raven does about it |
|---|---|---|
| 60–69% of agent failures reached and edited the *correct* function, then submitted a wrong patch; agents wrote gold-identical patches and later overwrote them | [Coherence Collapse](https://arxiv.org/abs/2603.24631) | Candidates are **immutable snapshots**, selected by evidence — a good edit is never overwritten |
| Patch-overfitting detectors lose to *random selection* in 71–96% of cases | [arXiv 2603.11262](https://arxiv.org/abs/2603.11262) | Selection uses **executed behaviour** only, never a static or learned judgement |
| Generated reproduction tests: 213/300 reproduced the bug, only 94 correctly verified the fix | [Agentless](https://arxiv.org/html/2407.01489) | The model's reproduction is split into **observation** (measured) and **expectation** (a belief, dropped if not a valid expression) |
| 77% of SWE-bench Verified instances admit a wrong patch that passes the tests; stronger tests cut top agents by 4–9 pts | [Probe to Generate](https://arxiv.org/abs/2604.01518) | Candidates act as each other's **mutants**; Crux finds the input that exposes the wrong one |
| Inputs are cheap, oracles (assertions) are the hard part | [FIXCHECK, ICST'24](https://conf.researchr.org/details/icst-2024/icst-2024-papers/17/Improving-Patch-Correctness-Analysis-via-Random-Testing-and-Large-Language-Models) | Oracle queries only where candidates **disagree**: usually 0–1 per task |
| Harness variance is 7.8× model variance; 22/81 runs killed at the time limit already had a passing patch | [Binding Constraint](https://arxiv.org/abs/2605.23950), [Harness isolation](https://arxiv.org/abs/2609.11987) | Deterministic tooling does the work; the run keeps its **best patch so far** |
| Qwen3-235B produces a successfully submitted patch in only 26.5% of attempts; DeepSeek caches prompt prefixes at ~1/10 the price | [SWE-Compass](https://arxiv.org/pdf/2511.05459), [DeepSeek](https://api-docs.deepseek.com/news/news0802/) | **One-shot structured calls** (no long tool loop on the main path), deterministic edit application, **stable prompt prefixes** |

## How a task runs

```
intake + ranked repo map (no model) → LOCALIZE (1 call) → PROBE (1 call; run on the original code)
→ CANDIDATES (3 calls, in parallel, shared cached prefix) → EXECUTE each: probe + targeted tests (no model)
→ CRUX: run survivors on the issue's example, type-aware variants of it, and calls harvested from the
  repo's tests; cluster by behaviour (no model) → ADJUDICATE the most informative disagreement (0–2 small calls)
→ SELECT (largest surviving cluster, smallest diff) → APPLY + VALIDATE → certificate
```

- **Typical cost:** 6 model calls. The scripted demo uses about 2.8k tokens; live use is expected in the tens of thousands, against 15–30 full-context calls for a ReAct loop.
- **If a round fails,** a deterministic diagnosis — edit didn't apply / behaviour unchanged (wrong location → next-ranked functions) / broke tests / missed the expectation — becomes targeted feedback for one more round, not a restart.
- **If Crux can't produce a surviving fix** (or the repo has no Python to localize), the ReAct agent loop takes over with Crux's findings and the remaining budget; failing that, the best candidate so far is kept, labelled partial.
- **Every run writes a Crux ledger** to `.raven/runs/<id>/report.md`: locations, probe before/after, every candidate and why it lived or died, the behaviour clusters, each crux with its options, verdict and evidence, the patch, and model usage by stage.

**Honest limits.** If every candidate shares the same wrong belief, there is nothing to disagree about; the certificate then says "unanimous", not "proven". Diversity comes from different location hypotheses and framings, not only temperature. Crux executes Python; other languages go straight to the agent loop.

## Quick start

```bash
git clone <this repo> && cd <this repo>
export AI_API_KEY="<your-api-key>"
make setup
make run                                        # TUI; it asks which repository to work on
```

Because `make run` starts inside Raven's own checkout, Raven never assumes the current directory is the target: it asks for a path (or a git URL, which it clones into `workspaces/`). To skip the question, name the repository up front, any one of:

```bash
make run ARGS="--repo /path/to/target-repo"
export RAVEN_REPO=/path/to/target-repo          # then: make run
```

**To test it the way the evaluators will** (including with a free local model), follow [TESTING.md](TESTING.md).

In a real terminal this launches the TUI (rich panels, live tool stream). `TERM=dumb` or a failing TUI falls back to a plain REPL automatically.

`make test` runs the full offline test suite (258 tests; no API key or network — the model is a scripted `FakeClient`).

```bash
make test               # offline unit + integration tests
make eval               # eval harness (toy_repo + crux_demo fixtures) — see Results below
make demo               # the Crux moment, offline, with the model's replies scripted
```

**Model.** `config.yaml` ships the model Raven was tested against end to end (`openai/gpt-oss-20b` on Groq's OpenAI-compatible endpoint). If the organisers prescribe another model or endpoint, set `RAVEN_MODEL` / `RAVEN_BASE_URL` — no code changes. The key is only ever read from `AI_API_KEY`; a scored (non-interactive) run without it stops with an error instead of quietly using the offline fake model.

## Supplying an issue

Every non-interactive path runs the same autonomous pipeline (understand → reproduce → fix → verify → report) and ends with a delimited `===== RAVEN RESULT =====` block:

```bash
# 1. Piped stdin
echo "average() returns 3.0 for [2,4,6]; expected 4" | make run ARGS="--repo /path/to/repo"

# 2. Environment variables
RAVEN_REPO=/path/to/repo RAVEN_ISSUE="average() is off by one" make run

# 3. Flags
make run ARGS="--repo /path/to/repo --issue 'average() is off by one'"
make run ARGS="--repo /path/to/repo --issue-file issue.txt"

# 4. Pasted into the TUI — issue-like text (long text, stack traces,
#    "steps to reproduce", a GitHub issue URL) is auto-detected and run
#    autonomously; afterwards the session returns to chat. /auto <task> forces it.
```

A GitHub issue URL anywhere in the task is expanded with the issue's title and body (fetched by the harness; `GITHUB_TOKEN` is used if set). `--repo` also accepts a git URL, which is cloned. `--strategy` overrides `executor.strategy` (`single_loop` | `plan_execute` | `delegated`) for one run.

## GitHub

GitHub is Raven's input layer — the same Crux pipeline runs on the result. The flow is **connect → choose repo → choose issue → run**, entirely inside the TUI:

```
/gh login                 # or: export GITHUB_TOKEN=..., or an existing `gh auth login` is used automatically
/gh repos [filter]        # every repo you can access: owned, collaborator, organisation (Tab completes names)
/gh use <#|owner/name>    # clone + an isolated worktree on branch raven/session-<time>
/gh issues [filter]       # open issues of that repo
/gh issue 42              # fresh worktree for #42, then the agent runs on it; prints the patch path
```

Pasting a GitHub issue URL into the chat does the same. Non-interactively:

```bash
make run ARGS="--issue https://github.com/owner/repo/issues/42"   # fetches the repo itself; public repos need no login
```

- **Isolation:** `workspaces/<owner>__<repo>/base` is a clone Raven never edits; every task gets its own git worktree and branch. Nothing is ever pushed — the result is the worktree's diff, saved as `patch.diff` next to the run report.
- **Context:** the issue's title, labels, body, non-bot discussion (capped) and linked PRs/issues become the task; recent commits touching the file being fixed become adjudication evidence. Nothing else from GitHub is sent to the model.
- **Security:** tokens come from the environment, the device flow (saved at `~/.config/raven/github.json`, mode 0600), or the GitHub CLI — never source or config. The token goes only to api.github.com and to git via environment config (not `.git/config`, a URL, or argv), never into prompts or reports; a rejected token is cleared. Repository names, branches and issue numbers are validated before they reach git; issue text is fenced as untrusted description and never executed.
- **Setup:** only needed for private repositories — see `.env.example` (`GITHUB_TOKEN`, or `RAVEN_GITHUB_CLIENT_ID` for `/gh login`).

## Conversation layer

Modes: **Chat** (default, read-only, uses tools to answer questions about the repo) · **Plan** (`/plan <task>`, shows a plan and waits) · **Act** (`/act`, executes the exact plan you were shown — it does not silently recompute one) · **Autonomous** (`/auto <task>`, or auto-detected) · **Review** (`/review`, an independent critique of the current diff).

Slash commands: `/help /plan /act /auto /review /diff /undo /checkpoints /evidence /memory /lessons /budget /config /model /repo /chat /clear /exit`. These are implemented once in `raven/session/manager.py` and shared identically by both the TUI and the plain REPL — there is no behavioral difference between them beyond rendering.

## Architecture

```
raven/
├── cli.py · config.py · prompts.py           entry point, config loading, versioned prompt loader
├── github.py · workspace.py · intake.py       GitHub auth/discovery/issues, isolated worktrees, task intake
├── crux/           issue · repomap · probe · candidates · patching · regression · disagree ·
│                   adjudicate · certificate · pipeline      the default strategy (see above)
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
tests/                 258 offline tests (FakeClient) + tests/fixtures/{toy_repo,crux_demo}
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

**Resolved: 16/16 · avg tokens: 3312 · avg tool calls: 2.4**, split across `crux` (2 tasks), `single_loop` (8), `plan_execute` (3), and `delegated` (3) strategies, and task types bug_fix/feature/refactor/test_writing/question. `t14` is graded the way the hackathon likely grades: an issue-style goal with no test named, scored by a **hidden test** the agent never sees (written in only after the run), and it additionally requires the reproduction to be verified fail→pass.

### Live benchmark (real model, no scripted replies)

`evals/live_eval.py` runs `evals/live_tasks.yaml` — 8 realistic bugs in `tests/fixtures/bugbench`, whose visible tests pass on the buggy code — against any OpenAI-compatible endpoint, graded **only by hidden tests**. First run, local **Qwen2.5-Coder 3B** via Ollama on an 8 GB laptop (7 of 8 tasks completed; `evals/results/live_run_3b.txt`):

| | Crux | agent loop (`single_loop`) |
|---|---|---|
| hidden-test passes | **4 / 7** | 1 / 7 |
| wrong patches reported as RESOLVED | 0 | 0 |
| cost when resolved | 5 calls · ~2.6k tokens · ~90 s | typically 21 calls · ~40k tokens · ~5 min |

Two of Crux's passes (truncate, chunk) were correct fixes that it labelled *partial* after an unnecessary fallback; this run predates the timeout-masking fix, and closing that gap is the next tuning target. A 3B model is far weaker than the evaluator's Qwen/DeepSeek, so treat this as a floor.

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
- `raven/learn/tune.py` (`make evolve` doesn't cover this, run directly: `python -m raven.learn.tune`) is the same story for numeric config knobs (successive halving over `half_life`/`max_replans`). Every `config.yaml` key is now read by the run (`RavenConfig.run_kwargs()`; `tests/test_budget.py` fails if a key is added that nothing reads), so a tuned `config.tuned.yaml` takes effect end to end.
- The `delegated` and `plan_execute` executor strategies are both implemented and covered by tests/evals, but neither is the `config.yaml` default over `single_loop` — promoting either needs a real ablation against the prescribed model, which FakeClient-scripted evals cannot provide (see the `# PLAN-DECISION` comments in `raven/core/orchestrator.py`).

## Security

- `AI_API_KEY` is read only from the environment — never from config, code, or the Makefile, and never printed. It is scrubbed (with anything matching `KEY`/`TOKEN`/`SECRET`) from every subprocess that runs the target repo's code: tests, tracer, coverage, shell.
- File tools are jailed to the target repository; `create` never overwrites; `.env` files and private keys are never read, searched, or listed (anything read is sent to the model provider).
- Editing an *existing* test file is denied in every mode (creating new test files is allowed).
- The `shell` tool runs **one** allowlisted command (`python <script>`, `python -m pytest`, `pytest`, `ls`, `cat`, `grep`, `echo`, `pwd`, `find`) **without a shell**: chaining, pipes, redirection and substitution are rejected, path arguments must stay inside the repo, `find -delete/-exec` are refused, and on timeout the whole process group is killed. `rm`, `curl`/`wget`, `pip`/`npm`, `git`, `sudo`, shells, … are denied in every mode, even with approval (`raven/tools/policy.py`).
- Honest limit: this is not an OS sandbox for the target's *own* code — running its tests or a script in it executes that code with normal permissions, as any test runner does.
- Every write is checkpointed first; a failed or aborted run restores the tree exactly. Raven's own state (`.raven/`) is excluded via `.git/info/exclude`, and running tests writes no `__pycache__`/`.pytest_cache`, so the final tree holds only the patch.

## Limitations

- Eval numbers come from `FakeClient`-scripted runs (they prove the pipeline, not model quality). Live runs against gpt-oss-20b were used to find and fix real failures (native tool-call rejections, format quirks), but there is no live eval curve yet.
- Test-based verification is pytest-only. In other stacks Raven still edits and reports, but a run can only be **RESOLVED (unverified)** — it is never reported as plain RESOLVED without test evidence.
- Target tests run with the target repo's own interpreter (`$RAVEN_TARGET_PYTHON`, else its `.venv`/`venv`, else `python3` on PATH, else Raven's) — whichever can import pytest. If none has the target's dependencies, its tests error before and after alike and the evidence shows that.
- Symbol indexing is Python-only (other languages: file tree + text search).
- Context compaction, mutation testing and the disagreement check from the plan are not built; the corresponding config keys were removed rather than left as dead switches.
- `delegated`/`plan_execute` are not the shipped default; see above.

## Submission checklist (plan §22)

- [x] `make setup && make run` works from a clean checkout; `make test` works offline without a key (verified on a fresh copy of the tree with no `.venv`/`.raven`, on Python 3.9.6 and 3.12: `make setup` OK, `make test` 244/244, `make demo` OK; `make run` refuses to start a scored run without `AI_API_KEY` or without a target repository, with instructions; a piped issue runs the full pipeline and leaves only the patch)
- [x] `AI_API_KEY` from environment only; `.env.example` has an empty value; no secrets in the repo
- [x] Model/endpoint defined in config, overridable by env (`RAVEN_MODEL`, `RAVEN_BASE_URL`)
- [x] Seed, temperature, frozen base config documented (`config.yaml`); `config.tuned.yaml`/`prompts.tuned.yaml` are supported override paths, not present by default (no genuine tuning run has been done — see above)
- [x] Issue input works via flag, file, stdin, and TUI/REPL paste
- [x] Autonomous run leaves only the intended patch; report written; result block printed
- [x] Integrity guarantees (no network/push/history-rewrite, env scrubbing, path jailing) enforced in code
- [x] README has quick start, architecture, sample report, eval results, and limitations
- [ ] **Not applicable yet**: "prescribed model" / "organiser endpoint" — this hackathon detail isn't available in this environment; `RAVEN_MODEL`/`RAVEN_BASE_URL` make swapping to it a two-env-var change
- [ ] Real eval/evolution curve against a live model — pending `AI_API_KEY` and real budget
