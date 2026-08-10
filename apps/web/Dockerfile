# syntax=docker/dockerfile:1
#
# Two targets:
#   dev   — `next dev`, for docker compose with the source bind-mounted
#   prod  — Next.js standalone server, non-root, no package manager in the image
#
# NEXT_PUBLIC_* values are inlined into the browser bundle at build time, so the
# API URL is a build argument, not a runtime environment variable:
#   docker build --target prod --build-arg NEXT_PUBLIC_API_URL=https://api.example.com apps/web

FROM node:22-bookworm-slim AS base
ENV PNPM_HOME=/pnpm \
    PATH="/pnpm:${PATH}" \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app

# --------------------------------------------------------------------------
# Dependencies — cached on the manifests alone.
# --------------------------------------------------------------------------
FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

# --------------------------------------------------------------------------
# dev — hot reload.
# --------------------------------------------------------------------------
FROM base AS dev
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN chown -R node:node /app
USER node
EXPOSE 3000
CMD ["pnpm", "dev"]

# --------------------------------------------------------------------------
# Production build.
# --------------------------------------------------------------------------
FROM base AS build
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

# --------------------------------------------------------------------------
# prod — standalone server only: no pnpm, no source, no dev dependencies.
# --------------------------------------------------------------------------
FROM node:22-bookworm-slim AS prod
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app

WORKDIR /app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
# There is no public/ directory yet. Add
#   COPY --from=build --chown=app:app /app/public ./public
# the moment one appears, or its assets will 404 in the container only.

USER app
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
