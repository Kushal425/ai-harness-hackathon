PY ?= python3
VENV := .venv
BIN := $(VENV)/bin
.PHONY: setup run test eval evolve clean

setup:
	@echo "========================================="
	@echo "  Raven - Setup"
	@echo "========================================="
	@if ! command -v $(PY) >/dev/null 2>&1; then \
		echo "Error: Python 3 is required but was not found."; \
		exit 1; \
	fi
	@echo "Using Python: $$($(PY) --version)"
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		$(PY) -m venv $(VENV); \
	fi
	@$(BIN)/pip install --upgrade pip >/dev/null
	@$(BIN)/pip install -e ".[dev]" >/dev/null
	@command -v rg >/dev/null 2>&1 || echo "ripgrep not found: search tool will use its pure-Python fallback"
	@echo "Setup complete. Start with: make run"

run:
	@if [ ! -x "$(BIN)/python" ]; then \
		echo "Error: environment not set up. Run: make setup"; \
		exit 1; \
	fi
	AI_API_KEY="$(AI_API_KEY)" $(BIN)/python -m raven $(ARGS)

test:
	@if [ ! -x "$(BIN)/python" ]; then \
		echo "Error: environment not set up. Run: make setup"; \
		exit 1; \
	fi
	$(BIN)/python -m pytest -q tests

eval:
	AI_API_KEY="$(AI_API_KEY)" $(BIN)/python evals/run_evals.py $(ARGS)

evolve:
	AI_API_KEY="$(AI_API_KEY)" $(BIN)/python -m raven.learn.evolve $(ARGS)

clean:
	rm -rf $(VENV) .raven build dist *.egg-info evals/work
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "Clean complete."
