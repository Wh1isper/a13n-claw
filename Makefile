SHELL := /bin/bash
.PHONY: help install format lint typecheck deps-check test check check-all build artifact-check docs-serve docs-build docs-check workflow-check image-check console-install console-check console-build console-dev serve
help: ## List development commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'
install: ## Install locked dependencies and Git hooks
	uv sync --locked
	pnpm --dir docs install --frozen-lockfile
	$(MAKE) console-install
	uv run --locked pre-commit install
format: ## Format tracked source files
	uv run --locked pre-commit run --all-files
lint: ## Check Python and Markdown formatting
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked mdformat --check --number README.md AGENTS.md CONTRIBUTING.md DEVELOPMENT.md MAINTAINERS.md SECURITY.md docs/content spec .github .agents

typecheck: ## Type-check Python sources
	uv run --locked pyright

deps-check: ## Check Python dependency declarations
	uv run --locked deptry a13n_claw

test: ## Run tests without external service credentials
	uv run --locked pytest

check: lint typecheck deps-check docs-check console-check ## Run static checks
check-all: check test build artifact-check docs-build workflow-check image-check ## Run the complete CI-equivalent gates (Docker for workflows)
console-install: ## Install locked console dependencies
	pnpm --dir console install --frozen-lockfile
console-check: ## Type-check and format-check the console
	pnpm --dir console check
console-build: console-install ## Build the console into Python package assets
	pnpm --dir console build
console-dev: ## Serve the console with hot reload on loopback port 5173
	pnpm --dir console dev
serve: ## Run the backend and built Console on loopback port 8080
	uv run --locked a13n-claw serve
build: console-build ## Build the console, wheel, and source distribution
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

image-check: build ## Build and smoke-test the non-root runtime image
	docker build -f deploy/docker/Dockerfile -t a13n-claw:check .
	docker run --rm a13n-claw:check --version
	uv run --locked python scripts/check_image.py
