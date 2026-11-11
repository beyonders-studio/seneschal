# Repository Guidelines

Seneschal is a family of business apps, each a container plug-in for
[Initiative](https://github.com/beyonders-studio/initiative). Hoard (inventory
and production) is the only app being built.

Keep planning and design documents in `history/` at the repository root, which
is gitignored.

## Layout

| Path | What | Names |
|---|---|---|
| `apps/hoard/backend` | The Hoard service: FastAPI, SQLModel, asyncpg, Alembic | Python package `hoard`, project `hoard` |
| `apps/hoard/frontend` | Hoard's pages, which Initiative shows in its iframe: Vite, React, TypeScript | npm package `@seneschal/hoard` |
| `packages/core` | What every app's backend shares: organization isolation, the Initiative connection (token checks, handoff, hooks, statements) | Python package `seneschal_core`, project `seneschal-core` |
| `packages/ui` | Initiative's UI kit, copied whole by the kit's own `src/ui/refresh.mjs`. Nobody edits the copy | npm package `@seneschal/ui` |
| `scripts/ci/check` | The fast checks CI and the pre-push hook run | |

Each app has its own Docker image, database, plug-in manifest, `VERSION` and
`CHANGELOG.md` (`apps/hoard/VERSION`, `apps/hoard/CHANGELOG.md`).

## Commands

- `uv sync`: the Python workspace (root `pyproject.toml`), one `uv.lock`.
- `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`,
  `uv run pytest`.
- `pnpm install`, then from the root: `pnpm lint` (Biome), `pnpm typecheck`,
  `pnpm test` (Vitest), `pnpm build`, `pnpm check:kit` (the kit copy is the
  stamped Initiative revision's).
- `docker compose up -d db`: Postgres 17 for dev and tests, on port 5236.
- Ports in dev: Hoard's backend on 8300, its Vite dev server on 5300.

## How an app meets Initiative

- **One image per app** serves its API under `/api/v1` and its built pages
  from the same origin.
- **The plug-in platform's routes** sit outside `/api/v1`, at the paths the
  protocol fixes: `POST /v1/endpoints` and `POST /v1/hooks/{name}`, each
  carrying an Initiative-signed context or lifecycle token.
- **Pages** receive Initiative's one-minute handoff over the parent bridge,
  exchange it at `POST /api/v1/auth/handoff/exchange` for the app's own signed
  session, and read who is signed in at `GET /api/v1/auth/me`.
- The plug-in contract is the plug-in SDK's
  ([initiative-plugin-sdk](https://github.com/beyonders-studio/initiative-plugin-sdk)).
- `scripts/ci/check`: what CI runs, for what the branch changed against
  `origin/dev`; `scripts/ci/check all` runs everything.

## The database

**Two logins, never one.**

- `hoard_owner` (`HOARD_DATABASE_URL`) runs migrations and owns the tables. It
  never serves a request.
- `hoard_app` (`HOARD_DATABASE_URL_APP`) serves requests. It is not a
  superuser, does not bypass row security, and owns nothing. Hoard refuses to
  start on a serving login that breaks any of these.

**One organization per community install.** Every table but the organization
registry carries `organization_id`, references the registry, and has row
security enabled and forced with one policy: `organization_id` equals the
transaction's organization (`app.organization_id`). An unset organization
matches no row. The organization is set transaction-locally at the start of
every transaction, and comes only from a token Initiative signed: never from a
path, query, body or header. A catalog test fails on any table that lacks the
column, the forced row security or the policy.

**Tests** read `TEST_POSTGRES_URL`, a superuser URL (default
`postgresql://postgres:postgres@localhost:5236/postgres`), and each run creates
its own database and both roles. Tests run as the serving login wherever they
check access.

## Rules

- pnpm, not npm. uv, not pip.
- Tests sit beside their source: `thing_test.py`, `thing.test.ts(x)`.
- Every word a person reads is translated from day one: react-i18next on the
  frontend (`public/locales/en/<namespace>.json`), error codes rather than
  sentences from the backend.
- Hoard stores no documents. Files stay in Initiative and are linked by id.
- Hoard has no till and no sales pipeline.
- Name no company or product in code, comments, commits or pull requests, except
  the stores a connector talks to.
- This repository is public: comments explain what code does, never how a
  protection could be defeated.
- Commits: an imperative first line of 50 characters or fewer. Pull requests
  target `dev`.
- Python: 3.12, 4-space indentation, full type hints, ruff and ty clean.
  TypeScript: Biome clean, strict.
