# Contributing to Geospatial Digital Twin Platform

Thanks for considering a contribution. This project is a stdlib-only-core Python codebase (~115k lines, 37 modules), so a few conventions matter more here than in a typical project — please read the short list below before opening a PR.

## Ground rules

- **Core stays stdlib-only.** Anything that needs a third-party library must go behind an optional extra in `pyproject.toml`, be imported lazily, and either fall back to a built-in heuristic or raise a clear, typed error (`UnsupportedFormatError`, `MqttBackendUnavailable`, `ProviderUnavailableError`, ...) — never fail silently. See [Error Handling & Reliability](README.md#error-handling--reliability) and [Engineering Principles](README.md#engineering-principles) in the root README.
- **Module ownership.** Each top-level folder (`hazard_data`, `mobility`, `digital_twin`, ...) is its own module. If you're adding meaningful new functionality to a module, consider adding or updating its `README.md` (24 of 37 modules currently have one).
- **Explicit over implicit.** Request validation returns typed `422` errors with a field-specific message, not bare `500`s. Follow the same pattern for new API routes in `app_shell/api.py`.

## Getting set up

```bash
git clone https://github.com/kaandevs-ops/geospatial-digital-twin.git
cd geospatial-digital-twin
pip install -e ".[dev]"
```

Install any extras relevant to what you're working on (see [Technology Stack](README.md#technology-stack) for the full list), e.g.:

```bash
pip install -e ".[ml,geo]"
```

## Before opening a PR

```bash
pytest                  # must pass
ruff check .             # lint — must pass
ruff format .             # formatting
mypy .                   # gradual typing — new/changed code should be typed where practical
```

If your change touches an optional-extra code path, **test it both with and without the extra installed** and confirm the fallback/error behavior still holds. Note in your PR description which extras you tested with/without.

## Making a pull request

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/your-feature`.
3. Make your changes, following the conventions above.
4. Make sure `pytest`, `ruff check .`, and `mypy .` all pass.
5. Open a PR against `main` using the PR template — describe the change and, if relevant, which extras you tested with/without.
6. CI (lint, type check, tests across Python 3.10–3.12, Docker build) runs automatically on the PR.

## Reporting bugs / requesting features

Please use the issue templates — they ask for the details (Python version, installed extras, affected module) that speed up triage.

## Code of conduct

Be respectful and constructive. Disagreements about technical approach are normal and welcome; personal attacks are not.
