# Contributing to Jarvis

## Development environment

```bash
make venv        # Python 3.11 virtualenv with the lightweight dependencies (no AI model needed)
make test        # unit and integration tests
make check       # lint + SAST + shellcheck + tests + dependency audit (what the CI runs)
uvx pre-commit install   # run the hooks on every commit
```

The full stack (camera, models, audio) is installed with `scripts/jarvis-install.sh` on the target machine
(see `INSTALL.md` and `docs/manuals/jarvis-software-documentation.pdf`).

## Conventions

- **Language**: identifiers, file names, comments, docstrings, log and error messages are in **US
  English**. User-facing documentation (`docs/`, Markdown and PDFs) is in US English; the web UI is translated (12 locales,
  `jarvis/web/static/i18n/`, reference `en-US.json`).
- **File header**: every source file starts with the standard header block (file, purpose, author,
  project, copyright) — copy it from an existing file.
- **Docstrings**: Google style for every module, class and non-trivial function; JSDoc in `app.js`.
- **Style**: `ruff` (configuration in `pyproject.toml`), 120 columns; no inline styles or scripts in the
  web UI (strict CSP).
- **Tests**: every behavior change comes with a test (`tests/`); tests must not need models or hardware.
- **Security**: never commit secrets, biometric data, databases or models (see `.gitignore`); secrets go
  to the `secrets` section / `jarvis.env`; any new setting that holds a credential uses the `secret` type.
- **Changelog**: add an entry under *Unreleased* in `CHANGELOG.md` (Keep a Changelog).

## Versioning

- The version lives **only** in `jarvis/VERSION` (Semantic Versioning). Never write it anywhere
  else: Python reads `jarvis.__version__`, packaging uses `[tool.setuptools.dynamic]`, the other
  scripts read the file.
- Bump it with every delivered batch: `make bump PART=patch|minor|major`. This moves the CHANGELOG
  `[Unreleased]` section under a dated heading. Then fill in the entry, regenerate the SBOM and
  PDFs, and tag `vX.Y.Z`.

## Shell scripts

Every bash script (existing and new) must:

- **Structure:** source `scripts/lib/jarvis-common.sh` (strict mode, ERR/EXIT traps, colors), be
  split into documented functions, and end with `main "$@"`.
- **Messages:** log through `log_info` / `log_ok` / `log_warn` / `log_error` / `log_step` and
  fail through `die MESSAGE CODE`. Colors turn off automatically (no TTY, `NO_COLOR`,
  `--no-color`).
- **Header:** document usage, environment variables, prerequisites and the numeric exit codes
  (0 OK, 1 runtime, 2 usage, 3 privileges, 4 prerequisites, 5 configuration, 6 network,
  7 state, 8 warnings).
- **Arguments:** parse them with `--help`; unknown options are usage errors (exit 2).
- **Quality:** be idempotent and pass `make shellcheck` with no warning.

## Commits and pull requests

- [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, `build:`, `ci:`, `chore:`, `security:`; imperative mood, English.
- One topic per pull request; the CI (lint, SAST, secrets, SCA, tests, SBOM, container scan) must be green.
- Security-sensitive changes (authentication, sessions, audit, secrets, systemd sandboxing, relay control)
  require a second review.
