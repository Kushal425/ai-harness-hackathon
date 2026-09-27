# Testing Raven the way the evaluators will

This follows the organisers' standard procedure (§5 and §12 of the submission guidelines) step by step. Each step also says how to try it with a **free local model** instead of a paid API key.

## 0. Before you start

- You need `git` and `python3` (3.9 or newer).
- For free local testing, install [Ollama](https://ollama.com) and pull a Qwen coder model: `ollama pull qwen2.5-coder:7b`, then make sure Ollama is running.

## 1. Get the repository

```bash
git clone https://github.com/Kushal425/ai-harness-hackathon.git raven-eval
cd raven-eval
```

## 2. Set the API key

The evaluator runs `export AI_API_KEY="<provided key>"`. For a free local model instead:

```bash
export AI_API_KEY=local
export RAVEN_BASE_URL=http://localhost:11434/v1
export RAVEN_MODEL=qwen2.5-coder:7b
```

The evaluator sets only `AI_API_KEY`. With a real DeepSeek or Qwen key, nothing else is needed: Raven detects the provider and model at startup and prints its choice. `RAVEN_BASE_URL`/`RAVEN_MODEL` are only for pointing it somewhere else, like the local model above.

## 3. Setup

```bash
make setup
```

Expect `Setup complete. Start with: make run`.

## 4. A target repository with real bugs

The evaluator brings their own repository and issue. As a stand-in, copy the benchmark repo; it has 8 realistic bugs whose visible tests still pass:

```bash
rm -rf /tmp/bugbench && cp -R tests/fixtures/bugbench /tmp/bugbench
cd /tmp/bugbench && git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init && cd -
```

## 5. Launch the harness

```bash
make run
```

The TUI starts. Because it was started inside Raven's own folder, it asks which repository to work on: type `/tmp/bugbench` and press Enter.

## 6. Supply the issue (every supported way)

**A. Paste it into the TUI.** For example:

```
clamp(5, 0, 10) returns 0 instead of 5. Values inside the range should come back unchanged, and clamp(15, 0, 10) should give 10.
```

Longer issue text is detected automatically; for a short one, prefix it with `/auto `.

**B. A GitHub issue URL.** No local repository needed; the repository is fetched into an isolated worktree:

```bash
make run ARGS="--issue https://github.com/OWNER/REPO/issues/123"
```

**C. Piped in:**

```bash
echo "dedupe([3, 1, 3, 2, 1]) gives [1, 2, 3] but should keep first-occurrence order: [3, 1, 2]" | make run ARGS="--repo /tmp/bugbench"
```

**D. As a flag or environment variable:**

```bash
make run ARGS="--repo /tmp/bugbench --issue 'chunk([1, 2, 3, 4, 5], 2) drops the last partial chunk [5]'"
RAVEN_REPO=/tmp/bugbench RAVEN_ISSUE="roman_to_int('IV') returns 6, should be 4" make run
```

More issues (with their hidden grader tests) are in `evals/live_tasks.yaml`.

## 7. What to look for

- **TUI:** a live spinner, then the Crux steps — locate, probe, candidate fixes typed out as diffs, behaviour clusters, the crux question, applying the fix — then a verification panel and a RESOLVED / UNRESOLVED badge.
- **Piped / flag mode:** the `===== RAVEN RESULT =====` … `===== END =====` block, with the report path.
- **The change:** `git -C /tmp/bugbench diff` should contain only the fix.
- **The reasoning:** the printed `report.md` has the Crux ledger — every candidate, the question asked, and the evidence behind the answer.

Reset between tries so each issue starts from the buggy code: `git -C /tmp/bugbench checkout -q .`

## 8. Tests

```bash
make test     # the full offline suite; no API key or network needed
make demo     # the offline Crux demo: the rejected fix fails the hidden grader test
```

## Optional: the automated live benchmark

Runs every issue in `evals/live_tasks.yaml` against a real model and grades each one only by hidden tests the model never sees:

```bash
.venv/bin/python evals/live_eval.py --model qwen2.5-coder:7b --strategies crux
.venv/bin/python evals/live_eval.py --model qwen2.5-coder:7b --tasks clamp_upper_bound,dedupe_order
```

## Notes

- A 7B model on an 8 GB laptop is slow (tens of seconds per call) and much weaker than the models the evaluators use, so some issues will end UNRESOLVED. That is expected; it does not mean the harness is broken.
- If a shell alias shadows `cp` on your machine, use `command cp` in step 4.
