# Raven

**An autonomous coding-agent harness that proves its fixes instead of claiming them.**
Built for the **LCC × DevClub AI Coding Harness Hackathon 2026**.

Give Raven a repository and an issue (typed, pasted, piped, or a GitHub issue URL). It finds the relevant code, reproduces the bug, writes several candidate fixes, **runs them against each other to find where they disagree**, settles that disagreement with evidence from the repository, and applies the fix that holds up — with a report showing exactly why.

> **Same model. Different harness.** Raven's edge doesn't come from a bigger model or more calls. It comes from spending the model's judgement only where executed evidence says it's needed.

---

## Contents

- [Quick start](#quick-start)
- [The idea: Crux](#the-idea-crux)
- [Key features](#key-features)
- [How a task runs](#how-a-task-runs)
- [Architecture](#architecture)
- [Model: DeepSeek & Qwen](#model-deepseek--qwen)
- [Using Raven](#using-raven)
- [Results](#results)
- [Submission guideline compliance](#submission-guideline-compliance)
- [Configuration](#configuration)
- [Security](#security)
- [Testing](#testing)
- [Limitations](#limitations)

---

## Quick start

This is exactly the organisers' standard evaluation procedure:

```bash
git clone https://github.com/Kushal425/ai-harness-hackathon.git
cd ai-harness-hackathon
export AI_API_KEY="<your DeepSeek or Qwen API key>"
make setup
make run
```

- `make setup` creates a virtual environment and installs everything (Python 3.9+).
- `make run` opens the terminal UI. Because it starts inside Raven's own folder, it asks **which repository to work on** — type a path, or a GitHub repo (`owner/name`), which is cloned into an isolated workspace.
- Then give it the issue — paste it into the chat. Any of these also work:

```bash
make run ARGS="--issue https://github.com/owner/repo/issues/42"            # fetches the repo itself
echo "clamp(5, 0, 10) returns 0 instead of 5" | make run ARGS="--repo /path/to/repo"
make run ARGS="--repo /path/to/repo --issue 'average() is off by one'"
RAVEN_REPO=/path/to/repo RAVEN_ISSUE="..." make run
```

Other commands:

```bash
make test     # 287 offline tests — no API key or network needed
make demo     # 30-second offline demo of the core idea (scripted model)
make eval     # offline eval suite (16 tasks)
make clean    # remove generated artefacts
```

A full walkthrough, including testing with a **free local model** (Ollama), is in [TESTING.md](TESTING.md).

---

## The idea: Crux

Most coding agents run one long loop: read files, edit, run tests, repeat, then trust whatever the last edit left. Raven does something different:

> **Don't ask the model whether a patch is right. Generate a few candidate fixes, run them against each other, and ask the model one concrete question — only about the single input where the candidates disagree — backed by evidence from the repository.**

`make demo` shows it (the scripted model gives the same result every time, no API key needed):

```
◆ probe    reproduced: chunk([1, 2, 3, 4, 5], 2) -> => [[1, 2], [3, 4]]
   ✓ c1 alive (2 changed lines) — stop the range at len(items)
   ✓ c2 alive (3 changed lines) — ceiling-divide to count chunks
   ✓ c3 alive (5 changed lines) — loop to the end
◆ cluster  3 surviving candidates -> 2 behaviour cluster(s) over 11 inputs
✦ crux     chunk([], 2)   [c1, c3] => []  vs  [c2] => [[]]
⚖ verdict  A: window() documents that empty input yields no windows; tests assert window([], 2) == []
★ select   selected c1 (2 changed lines)

hidden grader test (never shown to Raven):
  selected c1: PASS
  rejected c2: FAIL   (passed every visible test — only the crux caught it)
```

All three fixes pass the visible tests and the issue's own example. Only one input tells them apart, and Raven finds it automatically, asks one question about it, and answers it from the repository's own code and tests.

**Why this works** — every design choice comes from published results:

| Finding | What Raven does |
|---|---|
| Agents often reach the *correct* code and then overwrite it with a wrong patch (60–69% of failures, *Coherence Collapse*) | Each candidate fix is an immutable snapshot; the best one is selected, never overwritten |
| Tools that guess which patch is correct lose to random choice in 71–96% of cases (arXiv 2603.11262) | Selection is based only on **executed behaviour** |
| Model-written reproduction tests are often wrong (Agentless: 213/300 reproduced the bug, only 94 verified the fix) | The probe separates what the code *does* (measured) from what it *should* do (the model's belief, checked) |
| 77% of SWE-bench tasks admit a wrong patch that passes the visible tests (*Probe to Generate*) | Candidates act as each other's mutants; the crux input exposes the wrong one |
| The harness matters more than the model (harness variance 7.8× model variance) | Deterministic tools do the work; the model answers focused, one-shot questions |

---

## Key features

**Unique**
- **Crux: disagreement-driven patch selection** — the core idea above.
- **Evidence certificate** — every run writes a report showing each candidate, why it survived or died, the crux question, the evidence used, and the final patch.
- **Behaviour guard** — a fix is rejected if it starts crashing on inputs the original code handled (checked on the issue's example, automatic variants of it, and calls taken from the repo's own tests).
- **Honest verdicts** — Raven never reports success without evidence; a run with no tests to check against is labelled `RESOLVED (unverified)`.

**Coding-agent capabilities** (the problem statement's requirements)

| Requirement | How Raven does it |
|---|---|
| Understand the task | Extracts identifiers, stack frames, file paths and expected behaviour from the issue |
| Navigate the repository | A ranked code map (function index + import graph), so no blind file reading |
| Use tools intelligently | One-shot structured calls on the main path; a hardened agent loop with native tool calling as fallback |
| Manage context | Only the relevant functions are shown; stable prompt prefixes for provider caching |
| Orchestrate model calls | A fixed pipeline: ~6 targeted calls per issue instead of a 20–30 call loop |
| Recover from failures | Diagnoses *why* a round failed (wrong location, broken tests, bad edit, new crash) and adapts; falls back to the agent loop; always keeps the best fix so far |
| Correct, verified changes | Reproduction probe, targeted regression tests, crux adjudication, behaviour guard |
| Efficient use of resources | Enforced token and time budgets; adaptive number of candidates; early stop when candidates agree |

**Everything else**
- **Terminal UI** — a live view of the agent working: a status spinner, every code change typed out as a coloured diff, and the Crux steps as they happen.
- **GitHub integration** — sign in, browse your repositories, pick an issue, and run the agent on it in an isolated git worktree (nothing is ever pushed).
- **DeepSeek / Qwen auto-detection** — only `AI_API_KEY` is needed; Raven finds the provider and model.
- **Works everywhere** — falls back to a plain text interface when the terminal can't show the UI; piped input gives a clean result block.

---

## How a task runs

```
issue ──► issue card + ranked code map ──► LOCALIZE ──► PROBE ──► CANDIDATES (3, in parallel)
          (no model)                      (1 call)    (1 call)   (3 calls)
                                                                      │
                     ┌────────────────────────────────────────────────┘
                     ▼
          EXECUTE each candidate: probe + targeted tests + behaviour guard   (no model)
                     │
      no survivor? ──┴──► diagnose (wrong place? broke tests? crashed?) ──► one more round
                     │
                     ▼
          CRUX: cluster survivors by behaviour ──► ask about the one input that separates them
                     │                               (0–2 small calls)
                     ▼
          SELECT ──► APPLY ──► VALIDATE ──► report + certificate
```

If Crux can't produce a fix (for example, the repository isn't Python), the agent loop takes over with everything Crux learned. If that also fails, the best candidate so far is kept, labelled *partial*.

A typical solved issue costs about **6 model calls**.

---

## Architecture

```
                        ┌──────────────────────────────────────────┐
  make run  ──────────► │  CLI / TUI / plain REPL   (raven/ui, cli) │
                        └───────────────┬──────────────────────────┘
                                        │ repo + issue (path, paste, pipe, GitHub URL)
                        ┌───────────────▼──────────────────────────┐
                        │  Intake          intake.py · github.py    │  resolve the target repo, fetch
                        │  Workspaces      workspace.py             │  issues, isolated git worktrees
                        └───────────────┬──────────────────────────┘
                        ┌───────────────▼──────────────────────────┐
                        │  Orchestrator    core/orchestrator.py     │  strategy, budgets, verdict, report
                        └───────┬───────────────────────┬──────────┘
                ┌───────────────▼─────────┐   ┌─────────▼──────────────────┐
                │  Crux (default)  crux/  │   │  Agent loop (fallback)     │
                │  probe · candidates ·   │   │  core/executor.py + tools/ │
                │  disagree · adjudicate  │   │  recovery/ · context/      │
                └───────────────┬─────────┘   └─────────┬──────────────────┘
                        ┌───────▼───────────────────────▼──────────┐
                        │  LLM gateway     llm/   retries, native   │  DeepSeek / Qwen auto-detected
                        │                  tool calls, quirks       │  from AI_API_KEY
                        └───────────────────────────────────────────┘
```

```
raven/
├── cli.py · config.py · intake.py     entry point, configuration, which repo / which issue
├── github.py · workspace.py           GitHub sign-in, repos, issues; isolated git worktrees
├── crux/                              the core pipeline
│   ├── issue.py · repomap.py            issue card, ranked code map
│   ├── probe.py                         runs expressions in the target repo's own Python
│   ├── candidates.py · patching.py      one-shot fix proposals, safe edit application
│   ├── regression.py                    only the tests that exercise the changed code
│   ├── disagree.py · adjudicate.py      input generation, behaviour clusters, the crux question
│   ├── certificate.py · pipeline.py     evidence report, the loop itself
├── core/                              orchestrator, budgets, verdicts, agent loop
├── llm/                               gateway, OpenAI-compatible client, provider detection
├── tools/                             read, search, edit, create, tests, restricted shell, git
├── recovery/ · context/ · verify/     checkpoints, context decay, tracing & fault localisation
├── session/ · ui/                     slash commands, terminal UI, plain REPL
└── report/ · memory/ · learn/         run reports, lessons, offline prompt tuning
prompts/base.yaml                      every prompt the model sees, versioned
config.yaml                            model, strategy, budgets — every key is used
evals/                                 eval tasks, offline + live benchmarks, make demo
tests/                                 287 offline tests + fixture repositories
```

---

## Model: DeepSeek & Qwen

The evaluation uses **DeepSeek and Qwen APIs**, and supplies only `AI_API_KEY`. Raven handles the rest at startup:

1. **Finds the provider** that accepts the key, using a free `GET /models` call on each endpoint in `config.yaml`: DeepSeek, then Alibaba Model Studio (DashScope) international, US and China.
2. **Picks the model** — the first of that provider's preferred models the key can access (e.g. `deepseek-v4-pro`, `qwen3-coder-plus`, `qwen3.8-max`), otherwise the best coding/chat model it lists. Vision, embedding, audio, reasoning-only and stream-only models are never picked.
3. **Prints the choice**, e.g. `[raven] model: deepseek-v4-pro via api.deepseek.com (auto-detected, provider deepseek)`.

Provider-specific settings are applied automatically (DeepSeek: thinking mode off; Qwen: `enable_thinking: false`, which DashScope requires for non-streaming calls). If an endpoint rejects any optional field, Raven drops it and retries rather than failing.

To pin a model instead: `export RAVEN_BASE_URL=... RAVEN_MODEL=...`, or `RAVEN_PROVIDER=deepseek|qwen`, or edit `config.yaml`. Settings are fixed for reproducibility: temperature 0, seed 7.

---

## Using Raven

**In the terminal UI**

| Command | What it does |
|---|---|
| *(just type)* | Chat about the code; paste an issue to run the agent on it |
| `/auto <task>` | Run the agent on a task |
| `/plan <task>` · `/act` | Show a plan first, then run exactly that plan |
| `/diff` · `/undo` · `/checkpoints` | Inspect or revert the agent's changes |
| `/evidence` · `/budget` · `/model` | Last run's evidence, token/time usage, model in use |
| `/repo <path or owner/name>` | Switch repository |
| `/help` · `/exit` | Help, quit |

**GitHub** (optional — only needed for private repositories; your `gh` CLI login is used automatically):

| Command | What it does |
|---|---|
| `/gh login` · `/gh status` · `/gh logout` | Sign in (OAuth device flow), check, sign out |
| `/gh repos [filter]` | Every repository you can access; Tab completes names |
| `/gh use <#/owner/name>` | Clone into an isolated worktree |
| `/gh issues [filter]` · `/gh issue 42` | List open issues; run the agent on one |

The result of a GitHub task is a `patch.diff` next to the run report — **nothing is ever pushed**.

---

## Results

**Offline evaluation** (`make eval`, scripted model, reproducible): **16/16 tasks resolved** across bug fixes, features, refactors, test writing and questions, including two Crux tasks graded by hidden tests.

**Live benchmark** (`evals/live_eval.py`): 8 realistic bugs whose visible tests pass on the buggy code, graded **only by hidden tests** the model never sees, run with free local Qwen models on an 8 GB laptop:

| Model | Crux | Plain agent loop |
|---|---|---|
| Qwen2.5-Coder 3B (7 tasks) | **4 / 7** fixed | 1 / 7 |
| Qwen2.5-Coder 7B (8 tasks) | **5 / 8** fixed | 3 / 8 |
| Cost of a clean Crux solve (7B) | ~6 calls · ~3k tokens · ~2 min | loop typically ~21 calls · ~40k tokens |

Each failure in these runs was analysed and fixed (the behaviour guard, tolerant expectation matching, and dropping expectations that copy the reported bug). A partial re-run on the 7B model then solved all 3 tasks it reached (including one that had failed before), in 5–7 calls each. These small local models are far weaker than the evaluation's DeepSeek/Qwen, so treat these numbers as a floor.

---

## Submission guideline compliance

| Guideline | How Raven meets it |
|---|---|
| §1 Makefile with `setup`, `run`, `test`, `clean` | ✅ At the repo root, plus `eval` and `demo` |
| §2, §8 `AI_API_KEY` only from the environment; no secrets | ✅ Read only from the environment, never printed; no keys anywhere in the repo or its history; `.env.example` has empty values |
| §3 Text-only models | ✅ Text in, text out; no image/audio/video |
| §4 Model defined in configuration; prescribed model used | ✅ `config.yaml` defines the DeepSeek/Qwen providers and preferred models; the one used is detected from the provided key and printed |
| §5, §12 `clone → export key → make setup → make run` | ✅ Verified on a fresh clone; the issue can be pasted, piped, passed as a flag/env var, or given as a GitHub URL |
| §6 TUI launched by `make run` | ✅ No other command needed; plain REPL fallback |
| §9 Environment independence | ✅ All dependencies installed by `make setup`; the target repo's tests run with its own interpreter |
| §10 Reproducible execution | ✅ Temperature 0, seed 7, deterministic tooling; settings documented in `config.yaml` |
| §13 Tested in a clean environment | ✅ Fresh clone on Python 3.9.6 and 3.12: `make setup`, `make test`, `make demo`, `make run` |

---

## Configuration

`config.yaml` (every key is used; a test enforces this):

| Section | What it controls |
|---|---|
| `llm` | Provider list for auto-detection, temperature, seed, timeouts, retries |
| `executor` | Strategy (`crux` default, or `single_loop`), number of candidates, parallel calls, loop limits |
| `verify` | Reproduction, tracing, behaviour checks |
| `budgets` | Token and time limits for a whole run (nudge at 80%, stop at 100%) |
| `ui` | Terminal UI on/off/auto |

Environment variables:

| Variable | Purpose |
|---|---|
| `AI_API_KEY` | **Required.** The model API key |
| `RAVEN_BASE_URL`, `RAVEN_MODEL`, `RAVEN_PROVIDER` | Optional: pin the endpoint/model instead of auto-detecting |
| `RAVEN_REPO`, `RAVEN_ISSUE` | Optional: target repository and issue text |
| `RAVEN_TARGET_PYTHON` | Optional: interpreter for the target repo's tests |
| `GITHUB_TOKEN`, `RAVEN_GITHUB_CLIENT_ID` | Optional: GitHub access for private repositories (see `.env.example`) |

---

## Security

- The API key is read only from `AI_API_KEY`, and is scrubbed from every process that runs the target repository's code.
- File tools are confined to the target repository, and never read `.env` files or private keys.
- The shell tool runs **one** allowlisted command without a shell: no chaining, pipes or redirection; paths must stay in the repository; `rm`, `curl`, `pip`, `git`, `sudo` and similar are always refused.
- Every change is checkpointed and can be undone; Raven's own files (`.raven/`) are excluded from git, so the final tree contains only the fix.
- GitHub tokens never appear in prompts, reports, `.git/config` or command lines.
- Issue text is treated as untrusted: it is never executed.

---

## Testing

```bash
make test                                   # 287 offline tests (no key, no network)
make demo                                   # the Crux demo
make eval                                   # 16 offline eval tasks
.venv/bin/python evals/live_eval.py --model qwen2.5-coder:7b --strategies crux   # live benchmark
```

[TESTING.md](TESTING.md) walks through the evaluators' procedure step by step, with a free local model.

---

## Limitations

- **Crux executes Python.** Other languages use the agent-loop fallback, which is less powerful.
- **Correlated mistakes:** if every candidate shares the same wrong belief, there is no disagreement to find; the report then says the candidates agreed, not that the fix is proven.
- **Not yet run on the evaluation's models:** live testing so far used local Qwen2.5-Coder 3B/7B. DeepSeek and Qwen API support is verified against their official documentation, their real endpoints, and local emulators of their documented behaviour, but not with a real key.
- **Key probing:** a generic `sk-…` key is offered to DeepSeek before Alibaba during detection. Set `RAVEN_PROVIDER` to avoid this.
