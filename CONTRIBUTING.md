# Contributing to ASO-Light

Thank you for your interest in contributing! This document covers how to set up a development environment, the coding conventions we follow, and the pull request process.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | [python.org](https://www.python.org/) |
| uv | latest | `pip install uv` |
| Node.js | 18+ | [nodejs.org](https://nodejs.org/) |
| npm | 9+ | bundled with Node |
| Docker | any | [docs.docker.com](https://docs.docker.com/get-docker/) (optional — for Postgres) |

## Quick Start

```bash
git clone https://github.com/overx-ai/aso-light.git
cd aso-light

# Install dependencies
make install

# Copy and fill env
cp backend/.env.example backend/.env
# Edit backend/.env — generate FERNET_KEY with:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Start backend (:8002) + frontend (:5173)
make dev
```

For PostgreSQL instead of SQLite:

```bash
make db-up      # starts Docker Postgres
make migrate    # runs Alembic migrations
```

## Project Structure

```
backend/app/
  api/v1/      FastAPI routes — one file per domain
  models/      SQLAlchemy 2.0 models (Mapped[type] style)
  schemas/     Pydantic request/response schemas
  services/    Business logic — ASC client, pricing, keywords, …
frontend/src/
  pages/       Full-page route components
  components/  Reusable UI components
  lib/hooks.ts All TanStack Query hooks (single source of truth for API calls)
docs/          Architecture and feature documentation
```

## Coding Conventions

### Python (backend)

- **Absolute imports only** — `from app.core.config import settings`, never relative `from .config`
- **Decimal for money** — never `float` for price math; use `Decimal` throughout
- **SQLAlchemy 2.0 style** — `select()` + `session.execute()` only; never legacy `.query()`
- **Error handling** — never expose raw Python errors to API responses; always use `HTTPException`
- **Ownership checks** — every per-app endpoint must verify `app.credential_id → credential.user_id == current_user_id` via `_get_verified_app` from `app/api/v1/_deps.py`
- **Model definitions** — use `Mapped[type]` annotations with `mapped_column`
- **Route paths** — use `""` not `"/"` for root endpoints (`redirect_slashes=False` is set globally)

### TypeScript (frontend)

- **TanStack Query hooks** — all API calls go through hooks in `src/lib/hooks.ts`; no raw `fetch` in components
- **Mantine v8 components** — use Mantine UI primitives; avoid adding additional UI libraries
- **Types** — define shared types in `src/types/index.ts`

### Both

- **No comments explaining what code does** — well-named identifiers do that; only comment *why* when non-obvious
- **DRY** — extract repeated logic into shared helpers/services before copy-pasting a third time

## Tests

```bash
# Backend
cd backend && uv run pytest

# Frontend (type-check + unit tests)
cd frontend && tsc --noEmit && npm test -- --run
```

All new API endpoints should have at least a smoke test. The `/backend/tests/` directory has examples.

## Pull Request Guidelines

1. **Small, focused PRs** — one feature or fix per PR makes review easier
2. **Branch naming** — `feat/`, `fix/`, `chore/`, `docs/` prefixes
3. **Tests** — add or update tests for new endpoints/services
4. **Docs** — update `docs/` if your change affects architecture or adds a new domain
5. **No secrets** — never commit `.env`, `.p8` keys, API tokens, or credentials
6. **Checklist** — the PR template will remind you

## Reporting Bugs

Open a [GitHub Issue](https://github.com/overx-ai/aso-light/issues) using the Bug Report template. Include:
- Steps to reproduce
- Expected vs actual behaviour
- Python/Node versions and whether you're using SQLite or PostgreSQL

## Suggesting Features

Open a [GitHub Issue](https://github.com/overx-ai/aso-light/issues) using the Feature Request template.

## License

By contributing, you agree that your contributions will be licensed under the [Apache 2.0 License](LICENSE).
