# Security Policy

## Supported versions

This project is currently in **Beta** (`0.x`). Only the latest release on the `main` branch is supported with security fixes.

| Version | Supported |
|---|---|
| latest (`main`) | ✅ |
| older tagged releases | ❌ |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting instead: go to the **Security** tab of this repository → **Report a vulnerability**. This opens a private advisory visible only to the maintainers until a fix is ready.

When reporting, please include:
- A description of the vulnerability and its potential impact
- Steps to reproduce (a minimal example is ideal)
- The affected module(s) (e.g. `app_shell`, `collaboration`, `persistence`)
- Whether it requires a specific optional extra to be installed (`ml`, `geo`, `cloud`, `postgres`, `iot`, `survey`, `llm`)

We aim to acknowledge reports within a reasonable timeframe and will credit reporters in the fix's release notes unless you prefer to remain anonymous.

## Scope notes specific to this project

- **`app_shell` is single-tenant by default** and does not wire in the `collaboration` auth layer (roles/tokens) automatically. Direct `app_shell` deployments should be treated as trusted-network-only unless you've integrated `collaboration/auth.py` and the WebSocket server yourself. This is a documented deployment limitation, not something to report as a vulnerability on its own — but auth *bypasses* in the `collaboration` layer itself are in scope.
- **Hazard/risk outputs** (`hazard_data.risk_scoring`) are explicitly indicative screening scores, not certified structural engineering assessments — this is a documented modeling limitation, not a security issue.
- **Request-size and rate limits** are enforced in `app_shell.api` / `security` — if you find a way to bypass these (e.g. a route missing the `Content-Length` bounds check), that is in scope.
