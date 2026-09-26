.PHONY: setup run test clean

PYTHON := python3
VENV := .venv
VENV_BIN := $(VENV)/bin
PYTHON_VENV := $(VENV_BIN)/python
PIP_VENV := $(VENV_BIN)/pip

setup:
	@echo "========================================="
	@echo "  AI Coding Harness - Setup"
	@echo "========================================="
	@echo ""

	@if ! command -v $(PYTHON) >/dev/null 2>&1; then \
		echo "Error: Python 3 is required but was not found."; \
		exit 1; \
	fi

	@echo "Using Python: $$($(PYTHON) --version)"

	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		$(PYTHON) -m venv $(VENV); \
	else \
		echo "Virtual environment already exists."; \
	fi

	@echo "Upgrading pip..."
	@$(PYTHON_VENV) -m pip install --upgrade pip

	@if [ -f requirements.txt ]; then \
		echo "Installing dependencies..."; \
		$(PIP_VENV) install -r requirements.txt; \
	else \
		echo "No requirements.txt found. Skipping dependency installation."; \
	fi

	@echo ""
	@echo "Setup complete."
	@echo "Run the harness with: make run"


run:
	@echo "========================================="
	@echo "  AI Coding Harness"
	@echo "========================================="
	@echo ""

	@if [ -z "$$AI_API_KEY" ]; then \
		echo "Error: AI_API_KEY is not set."; \
		echo "Set it with:"; \
		echo '  export AI_API_KEY="<provided-api-key>"'; \
		exit 1; \
	fi

	@if [ ! -x "$(PYTHON_VENV)" ]; then \
		echo "Error: Environment is not set up."; \
		echo "Run: make setup"; \
		exit 1; \
	fi

	@$(PYTHON_VENV) -m src.main


test:
	@echo "========================================="
	@echo "  AI Coding Harness - Tests"
	@echo "========================================="
	@echo ""

	@if [ ! -x "$(PYTHON_VENV)" ]; then \
		echo "Error: Environment is not set up."; \
		echo "Run: make setup"; \
		exit 1; \
	fi

	@$(PYTHON_VENV) -m pytest


clean:
	@echo "Cleaning generated files..."

	rm -rf $(VENV)
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf .agent_state
	rm -rf .workspace

	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

	@echo "Clean complete."