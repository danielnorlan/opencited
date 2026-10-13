# Development commands. `make check` runs the checks that CI runs, on one Python version.

FILES := $(shell git ls-files --cached --others --exclude-standard 2>/dev/null)

.PHONY: help setup check hooks typecheck test package format

help: ## Show the available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-10s %s\n", $$1, $$2}'

setup: ## Create the environment, install the git hooks and check the commit identity
	uv sync --all-groups
	uv run pre-commit install
	uv run python scripts/check_commit_message.py --identity

check: hooks typecheck test package ## Run the checks that CI runs

hooks: ## Run the pre-commit hooks on tracked and new files
	uv run pre-commit run --show-diff-on-failure --files $(FILES)

typecheck: ## Type-check with Pyright in strict mode
	uv run pyright

test: ## Run the test suite
	uv run pytest

package: ## Build the package and import it from the wheel
	rm -rf dist
	uv build --quiet
	uv run --isolated --no-project --with dist/*.whl python -c "import opencited"
	uv run --isolated --no-project --with dist/*.whl opencited --version

format: ## Format the code and apply safe lint fixes
	uv run ruff format
	uv run ruff check --fix
