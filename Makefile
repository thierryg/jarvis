# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : Makefile
# Purpose : Developer and DevSecOps entry points (lint, test, security scans, SBOM, docs)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
# Every target runs its tools through uv/uvx: nothing is installed globally.
# "make check" is the local equivalent of the CI pipeline (.github/workflows/ci.yml).

PYTHON  ?= 3.11
VENV    ?= .venv
PY      := $(VENV)/bin/python
UVX     := uvx -q
SRC     := jarvis tests scripts docs/diagrams/src

.DEFAULT_GOAL := help
.PHONY: version bump ui-check docker-start docker-status docker-stop docker-clean help venv lint format typecheck test cov sast audit secrets shellcheck sbom docs pdf check pre-commit clean

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Create the lightweight development virtualenv (API + tests, no AI models)
	uv venv --python $(PYTHON) $(VENV)
	uv pip install --python $(PY) -r requirements-dev.txt
	uv pip install --python $(PY) --no-deps -e .

lint: ## Static analysis (ruff: pyflakes, bugbear, imports, pyupgrade)
	$(UVX) ruff check $(SRC)

format: ## Apply the ruff fixes that are safe
	$(UVX) ruff check --fix $(SRC)

test: ## Unit and integration tests (no model, no hardware required)
	PYTHONPATH=. $(PY) -m pytest -q

cov: ## Tests with coverage report (term + coverage.xml)
	uv pip install --python $(PY) -q pytest-cov
	PYTHONPATH=. $(PY) -m pytest -q --cov=jarvis --cov-report=term-missing --cov-report=xml

sast: ## Static application security testing (bandit)
	$(UVX) bandit -q -c pyproject.toml -r jarvis scripts

audit: ## Known vulnerabilities in the Python dependencies (pip-audit, OSV/PyPI advisories)
	$(UVX) pip-audit -r requirements-dev.txt --progress-spinner off

secrets: ## Secret scanning of the working tree (gitleaks, requires the gitleaks binary)
	gitleaks dir --no-banner --redact . || gitleaks detect --no-git --no-banner --redact --source .

shellcheck: ## Lint the shell scripts
	$(UVX) --from shellcheck-py shellcheck scripts/*.sh scripts/lib/*.sh deploy/bin/* deploy/motd/jarvis-motd deploy/motd/10-jarvis deploy/ansible/*.sh deploy/ansible/tests/*.sh docker/*.sh

sbom: ## Regenerate and validate the CycloneDX SBOM (installed environment)
	./scripts/jarvis-regen-sbom.sh --no-pdf

pdf: ## Rebuild every PDF (diagrams, BOM/SBOM, manuals)
	uv run --no-project --with markdown python3 docs/diagrams/src/build.py

docs: pdf ## Alias of pdf

version: ## Print the software version (single source: jarvis/VERSION)
	@python3 scripts/jarvis-bump-version.py --show

# Usage: make bump PART=patch|minor|major|X.Y.Z  (updates jarvis/VERSION and the CHANGELOG)
bump: ## Bump the version and open the CHANGELOG entry (PART=patch|minor|major|X.Y.Z)
	python3 scripts/jarvis-bump-version.py $(PART)

ui-check: ## Real-browser UI check (Playwright + Google Chrome, screenshots in a temp dir)
	uv run --no-project --with playwright --with-editable . python tests/browser/ui_check.py

docker-start: ## Build the images and start Jarvis locally in Docker (https://localhost:8443/)
	./docker/jarvis-start.sh

docker-status: ## State of the local Docker test (containers, health, resources, web UI)
	./docker/jarvis-status.sh

docker-stop: ## Stop the local Docker test (data, models and image kept)
	./docker/jarvis-stop.sh

docker-clean: ## Stop the local Docker test and remove its image (--volumes/--purge: see the script)
	./docker/jarvis-clean.sh

pre-commit: ## Run every pre-commit hook on all files
	$(UVX) pre-commit run --all-files

check: lint sast shellcheck test audit ## Everything the CI runs locally (except container scans)

clean: ## Remove caches and build artifacts
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
