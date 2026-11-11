# Hoard's image: its API under /api/v1 and its built pages, from one origin.
# Built from the repository root:
#
#   docker build -f apps/hoard/Dockerfile .
#
# The image keeps the repository's layout under /app, so a path the backend
# finds from its own source (VERSION, CHANGELOG.md, static/) is the same here
# as in a checkout.

# The pages are the same files on every platform, so they are built once, on
# the builder's own platform.
FROM --platform=$BUILDPLATFORM node:26.12.1-alpine@sha256:40cd011b55415bc894e45b02fdd5a5dfde056921b7d23a64c74f80497af48797 AS frontend
WORKDIR /src
# pnpm is the version package.json's packageManager names, through corepack,
# which Node no longer ships. HUSKY=0: there is no repository to install hooks in.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    HUSKY=0
RUN npm install --global corepack@0.36.0 && corepack enable
# The manifests first, so the install is cached until the dependencies change.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/ui/package.json packages/ui/
COPY apps/hoard/frontend/package.json apps/hoard/frontend/
RUN pnpm install --frozen-lockfile
COPY packages/ui packages/ui
COPY apps/hoard/frontend apps/hoard/frontend
RUN pnpm --filter @seneschal/hoard build

# uv builds the environment, and stays in this stage: the runtime runs alembic
# and uvicorn from the environment and never calls uv. Both stages share the
# base image and /app, so the environment's interpreter links and its editable
# installs of the workspace's packages resolve the same in the runtime.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS backend
COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# The workspace's manifests first, so the dependencies are cached until they
# change; then the packages themselves.
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY apps/hoard/backend/pyproject.toml apps/hoard/backend/
RUN uv sync --frozen --package hoard --no-dev --no-install-workspace
COPY packages/core packages/core
COPY apps/hoard/backend apps/hoard/backend
RUN uv sync --frozen --package hoard --no-dev

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
LABEL org.opencontainers.image.title="hoard" \
      org.opencontainers.image.description="Hoard: inventory and production, a plug-in for Initiative" \
      org.opencontainers.image.source="https://github.com/beyonders-studio/seneschal" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
# Debian's security updates published since the base image was built.
# Publishing builds this stage afresh every time, so they are the current ones.
RUN apt-get update \
    && apt-get dist-upgrade -y \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 hoard
COPY --from=backend /app /app
COPY LICENSE /app/
COPY apps/hoard/VERSION apps/hoard/CHANGELOG.md /app/apps/hoard/
# The built pages, at static/ beside the hoard package.
COPY --from=frontend /src/apps/hoard/frontend/dist /app/apps/hoard/backend/static
WORKDIR /app/apps/hoard/backend
# The app only reads its files, so they stay root's.
USER hoard

EXPOSE 8300
# start-period covers the migrations and the app's start.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8300/api/v1/healthz').status != 200)"]
# Migrations as the owner login (HOARD_DATABASE_URL), then the server as the
# serving login (HOARD_DATABASE_URL_APP).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn hoard.main:app --host 0.0.0.0 --port 8300"]
