UV ?= uv
PNPM ?= pnpm
TEST_ARGS ?=

.PHONY: install lint format typecheck deps-check test frontend-install console-sdk console-check console-build console-dev db-check build artifact-check image-requirements image-check workflow-check docs-check docs-serve docs-build check check-all

IMAGE_TAG ?= a13n-claw:check
IMAGE_MODE ?= locked

install: frontend-install ## Install the development toolchain and Git hooks
	$(UV) sync --locked
	$(PNPM) --dir docs install --frozen-lockfile
	$(UV) run --locked pre-commit install

format:
	$(UV) run --locked pre-commit run --all-files

lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .

typecheck:
	$(UV) run --locked pyright

deps-check:
	$(UV) run --locked deptry .

test:
	$(UV) run --locked pytest $(TEST_ARGS)

frontend-install:
	$(PNPM) --dir frontend install --frozen-lockfile

console-sdk:
	$(UV) run --locked python -m a13n_service.console_sdk frontend/.console-sdk/index.d.ts

console-check: console-sdk
	$(PNPM) --dir frontend typecheck
	$(PNPM) --dir frontend test

console-build: frontend-install console-check
	$(PNPM) --dir frontend build

console-dev: frontend-install console-sdk
	$(PNPM) --dir frontend dev

db-check:
	$(UV) run --locked a13n-service migrate
	@$(UV) run --locked a13n-service apps db check --app a13n.claw --scratch-url "$$CLAW_TEST_DATABASE_URL"

build: console-build
	$(UV) build --clear

artifact-check:
	$(UV) run --locked python scripts/check_artifacts.py --locked-service

image-requirements:
	$(UV) run --locked python scripts/service_version.py --mode $(IMAGE_MODE)

image-check: build image-requirements
	@version=$$(python -c 'import json; print(json.load(open("build/service-selection.json"))["version"])'); \
	image=$$(python -c 'import json; print(json.load(open("build/service-selection.json"))["image"])'); \
	docker build -f deploy/docker/Dockerfile -t $(IMAGE_TAG) --build-arg SERVICE_VERSION="$$version" --build-arg SERVICE_IMAGE="$$image" .
	docker run --rm --entrypoint /app/.venv/bin/python $(IMAGE_TAG) -c 'import os; from a13n_claw.service_app.app import definition; assert os.geteuid() != 0; assert definition().app_id == "a13n.claw"'

workflow-check:
	docker run --rm -v "$(CURDIR):/repo" -w /repo rhysd/actionlint:1.7.12

docs-check:
	$(PNPM) --dir docs exec prettier --check .
	$(PNPM) --dir docs check

docs-serve:
	$(PNPM) --dir docs dev

docs-build:
	$(PNPM) --dir docs build

check: lint typecheck deps-check console-check

check-all: check test build artifact-check workflow-check docs-check docs-build
