SHELL := /bin/bash
.PHONY: help install format lint typecheck deps-check test check check-all build artifact-check docs-serve docs-build docs-check workflow-check image-check
help: ## List development commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'
install: ## Install locked dependencies and Git hooks
	uv sync --locked
	pnpm --dir docs install --frozen-lockfile
	uv run --locked pre-commit install
format: ## Format tracked source files
	uv run --locked pre-commit run --all-files
lint: ## Check Python and Markdown formatting
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked mdformat --check --number README.md AGENTS.md CONTRIBUTING.md DEVELOPMENT.md MAINTAINERS.md SECURITY.md docs/content spec .github

typecheck: ## Type-check Python sources
	uv run --locked pyright

deps-check: ## Check Python dependency declarations
	uv run --locked deptry src

test: ## Run offline tests
	uv run --locked pytest

check: lint typecheck deps-check docs-check ## Run static checks
check-all: check test build artifact-check docs-build workflow-check image-check ## Run the complete CI-equivalent gates (Docker for workflows)
build: ## Build Python wheel and source distribution
	uv build --clear
artifact-check: ## Install the wheel and rebuild the sdist in temporary environments
	uv run --locked python scripts/check_artifacts.py
docs-check: ## Check documentation formatting and types
	pnpm --dir docs exec prettier --check .
	pnpm --dir docs check

docs-serve: ## Serve documentation locally
	pnpm --dir docs dev

docs-build: ## Build the static documentation site and validate links
	pnpm --dir docs build

workflow-check: ## Validate GitHub Actions with actionlint
	docker run --rm -v "$(CURDIR):/repo" -w /repo rhysd/actionlint:1.7.12

image-check: build ## Build and smoke-test the non-root placeholder image
	docker build -f deploy/docker/Dockerfile -t a13n-claw:check .
	docker run --rm a13n-claw:check --version
