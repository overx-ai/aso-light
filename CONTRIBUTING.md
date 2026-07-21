# Contributing to ASO-Light

Thank you for your interest in contributing! This document covers how to set up a development environment, the coding conventions we follow, and the pull request process.

Please also read our [Code of Conduct](CODE_OF_CONDUCT.md). By participating you agree to its terms. Security issues belong in [SECURITY.md](SECURITY.md) — please don't open public issues for them.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Coding Conventions](#coding-conventions)
- [Database Migrations](#database-migrations)
- [Extension Points](#extension-points)
- [Tests](#tests)
- [Commit Messages](#commit-messages)
- [Pull Request Guidelines](#pull-request-guidelines)
- [Reporting Bugs and Suggesting Features](#reporting-bugs-and-suggesting-features)
- [License](#license)

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | [python.org](https://www.python.org/) |
| uv | latest | `pip install uv` |
| Node.js | 20+ | [nodejs.org](https://nodejs.org/) |
| npm | 9+ | bundled with Node |
| Docker | any | [docs.docker.com](https://docs.docker.com/get-docker/) — optional, for Postgres |

---

## Quick Start

```bash
git clone https://github.com/overx-ai/aso-light.git
cd aso-light

# Install all dependencies
make install

# Generate a Fernet key for credential encryption
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Copy the example env and fill in the generated key
cp backend/.env.example backend/.env
# Edit backend/.env — at minimum set FERNET_KEY to the value above

# Start backend (:8002) + frontend (:5173) with SQLite (no Docker needed)
make dev
```

Open <http://localhost:5173>. SQLite auto-creates on first run.

**Using PostgreSQL instead:**

```bash
make db-up      # starts Docker Compose Postgres
make migrate    # runs Alembic migrations
make dev        # start app
```

**Backend only / frontend only:**

```bash
make dev-backend    # backend on :8002, hot-reload
make dev-frontend   # frontend on :5173, HMR
```

---

## Project Structure

```
backend/
  app/
    api/v1/          FastAPI routes — one file per domain
    core/            Config (pydantic-settings), JWT/Fernet security helpers
    data/            Territory seed data (202 territories, alpha-2 / alpha-3 mapping)
    db/              Async SQLAlchemy session + bootstrap (migration + seed on startup)
    models/          SQLAlchemy 2.0 models (Mapped[type] style)
    mcp/             FastMCP server, PAT auth middleware, per-domain tool modules
    schemas/         Pydantic request / response schemas
    services/
      asc/           App Store Connect client, pricing, reviews, metadata, screenshots
      asa/           Apple Search Ads client, sync, paid-organic joins
      pricing/       Price calculators (PPP, BigMac, Netflix, Spotify, exchange rate, …)
      keywords/      Tracker, iTunes suggestions, cross-localization
      keyword_intel/ Volume + difficulty provider abstraction
      metadata/      Snapshot, bulk fan-out, AI translation, keyword coverage
      reviews/       Theme classifier, reply drafting
      growth/        Cross-domain recommendation engine
      indices/       Economic-index fetchers (PPP, BigMac, GDP, Netflix, Spotify)
      rates/         Exchange-rate client (api.overx.ai)
  alembic/           Database migrations
  tests/
frontend/
  src/
    pages/           Full-page route components
    components/      Reusable UI components
    lib/
      hooks.ts       All TanStack Query hooks — the only place API calls live
      api.ts         Axios instance + base URL config
    types/index.ts   Shared TypeScript types
docs/                Architecture and feature documentation
```

---

## Coding Conventions

### Python

- **Absolute imports only** — `from app.core.config import settings`, never `from .config import settings`
- **Decimal for money** — never `float` for price math; `Decimal` throughout
- **SQLAlchemy 2.0 style** — `select()` + `session.execute()` only; no legacy `.query()`
- **Error responses** — never expose raw Python errors; always raise `HTTPException` with a safe message
- **Ownership checks** — every per-app endpoint must call `_get_verified_app(app_id, user_id, session)` from `app/api/v1/_deps.py`; never skip it
- **Model definitions** — `Mapped[type]` annotations with `mapped_column`
- **Route paths** — `""` not `"/"` for root endpoints (`redirect_slashes=False` is set globally)
- **httpx** — use `.aclose()` not `.close()` on `AsyncClient`
- **ASC territory codes** — ASC returns alpha-3 (`ARE`, `USA`); the DB stores alpha-2 (`AE`, `US`); use `ALPHA2_TO_ALPHA3` from `app/data/territories.py` to convert

### TypeScript / React

- **TanStack Query hooks** — all API calls go through `src/lib/hooks.ts`; no raw `fetch`/`axios` calls in components
- **Mantine v8** — use Mantine UI primitives; avoid pulling in additional component libraries
- **Types** — add shared types to `src/types/index.ts`; keep component-local types inline

### General

- **Comments** — only add a comment when the *why* is non-obvious (a hidden constraint, a subtle invariant, a workaround). Don't comment what the code does — names do that.
- **DRY** — extract repeated logic into shared helpers/services; use the existing ABCs (see [Extension Points](#extension-points)) rather than adding ad-hoc logic in routes.

---

## Database Migrations

ASO-Light uses Alembic. The app runs `upgrade head` automatically on startup via `bootstrap_database()`, so you never need to migrate manually in development.

**Creating a new migration** after changing a model:

```bash
make migration msg="describe_your_change"
# expands to: cd backend && alembic revision --autogenerate -m "describe_your_change"
```

Review the generated file in `backend/alembic/versions/` before committing — autogenerate is helpful but not perfect. Never add irreversible destructive operations (e.g. `DROP COLUMN`) without handling existing data.

**Applying manually (e.g. against a production database):**

```bash
make migrate      # upgrade head
```

---

## Extension Points

The codebase has several ABCs designed to be extended without touching existing code.

### New pricing calculator

Subclass `ProportionalCalculator` from `app/services/pricing/calculator.py` and register it in the engine:

```python
# backend/app/services/pricing/my_index.py
from app.services.pricing.calculator import ProportionalCalculator

class MyIndexCalculator(ProportionalCalculator):
    index_type = "my_index"   # matches IndexType enum
```

All `ProportionalCalculator` subclasses (PPP, BigMac, Netflix, Spotify, exchange rate) share the same formula — override only what differs.

### New economic-index data source

Subclass `IndexFetcher` from `app/services/indices/base.py`:

```python
from app.services.indices.base import IndexFetcher

class MyFetcher(IndexFetcher):
    async def fetch(self) -> dict[str, float]: ...
```

Register it alongside the existing fetchers in `app/api/v1/indices.py`.

### New MCP tools

Add a module under `backend/app/mcp/tools/` and import it in `backend/app/mcp/__init__.py`. Follow the existing pattern:

- Call service classes directly — no HTTP hop
- Use `session_scope()` + `resolve_app()` for session and ownership
- Raise `ToolError` for user-visible errors
- Name tools `@mcp.tool(name="module_action")` — underscores, **not dots** (the Anthropic tool-name regex `^[a-zA-Z0-9_-]{1,64}$` rejects dots; Claude Desktop refuses a dotted server, Claude Code silently rewrites `.`→`_`)

---

## Tests

**Backend:**

```bash
cd backend && uv run pytest          # all tests
cd backend && uv run pytest -q -x    # fail fast
```

Tests use an isolated in-memory SQLite file configured in `tests/conftest.py` so they never touch your dev database.

**Frontend:**

```bash
cd frontend && npx tsc --noEmit      # type check
cd frontend && npm test -- --run     # vitest single run
cd frontend && npm test              # vitest watch mode
```

**CI** runs both suites on every push and pull request via GitHub Actions (`.github/workflows/ci.yml`). PRs must pass CI before merging.

**Coverage expectations:**

- New API endpoints: at least a smoke test covering the happy path and the ownership check
- New service classes / ABCs: unit tests with a real SQLite DB (see `tests/test_growth_recommendations.py` for the pattern)
- New frontend hooks: vitest tests alongside the hook file

---

## Commit Messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>
```

| Type | When to use |
|------|-------------|
| `feat` | New user-visible feature |
| `fix` | Bug fix |
| `refactor` | Code restructuring, no behaviour change |
| `test` | Adding or updating tests only |
| `docs` | Documentation only |
| `chore` | Build scripts, CI, dependency bumps |
| `perf` | Performance improvement |

- **Scope** = affected module, e.g. `pricing`, `mcp`, `keywords`, `metadata`
- **Description** = imperative mood, lowercase, no trailing period, ≤ 72 chars total
- **Body** (optional) = explain *why*, not *what*; reference issues as `Closes #123`

Examples:

```
feat(pricing): add per-locale price override pins
fix(mcp): return 401 when PAT is revoked mid-session
docs(contributing): add extension points section
```

---

## Pull Request Guidelines

1. **One concern per PR** — a feature, a bug fix, or a refactor; not all three at once.
2. **Branch naming** — use `feat/`, `fix/`, `chore/`, `docs/`, or `refactor/` followed by a short slug, e.g. `feat/price-override-pins`.
3. **Tests** — add or update tests; CI must pass.
4. **Docs** — update `docs/` if your change affects architecture or introduces a new domain; add an entry to `docs/INDEX.md` for new documentation files.
5. **No secrets** — never commit `.env`, `.p8` keys, API tokens, or any credentials. The `.gitignore` already excludes them.
6. **PR template** — the template in `.github/pull_request_template.md` prompts for a summary and test plan; fill it in.
7. **Draft PRs** — open as a draft for early feedback before requesting review.

---

## Reporting Bugs and Suggesting Features

- **Bug** — open a [GitHub Issue](https://github.com/overx-ai/aso-light/issues) using the **Bug Report** template. Include steps to reproduce, expected vs actual behaviour, and your environment (Python/Node versions, SQLite or Postgres).
- **Feature request** — open a [GitHub Issue](https://github.com/overx-ai/aso-light/issues) using the **Feature Request** template.
- **Security vulnerability** — see [SECURITY.md](SECURITY.md). Do not open a public issue.

---

## License

By contributing you agree that your contributions will be licensed under the [Apache 2.0 License](LICENSE).
