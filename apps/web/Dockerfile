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

# **No default, and the previous one was `http://localhost:8000`.** A build that
# forgot this argument produced an image whose browser bundle pointed at the
# machine of whoever happened to open it: the first deployed page load showed
# "Could not reach the API at http://localhost:8000" while a perfectly healthy
# API ran a few metres away. A default that is *somebody's laptop* is worse than
# no default, because it fails somewhere the build cannot see.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

# **`entra` unless told otherwise**, matching `config.py`'s reasoning: the weaker
# mode must always be something someone chose. Shipped as `dev` by omission once,
# and a public page offered to mint a token for any name typed into it.
ARG NEXT_PUBLIC_AUTH_MODE=entra
ENV NEXT_PUBLIC_AUTH_MODE=${NEXT_PUBLIC_AUTH_MODE}

# The second half of that guard. `config.ts` refuses dev sign-in unless the build
# says local or ci, so this defaulting to `prod` means an image has to be built
# deliberately for a laptop before it can render a name box.
ARG NEXT_PUBLIC_ENV=prod
ENV NEXT_PUBLIC_ENV=${NEXT_PUBLIC_ENV}

# Public identifiers, not secrets: a tenant id and a client id are published in
# every SPA that uses them, and the PKCE flow has no client secret at all.
ARG NEXT_PUBLIC_ENTRA_AUTHORITY=
ARG NEXT_PUBLIC_ENTRA_CLIENT_ID=
ARG NEXT_PUBLIC_API_SCOPE=
ENV NEXT_PUBLIC_ENTRA_AUTHORITY=${NEXT_PUBLIC_ENTRA_AUTHORITY}     NEXT_PUBLIC_ENTRA_CLIENT_ID=${NEXT_PUBLIC_ENTRA_CLIENT_ID}     NEXT_PUBLIC_API_SCOPE=${NEXT_PUBLIC_API_SCOPE}

# **Refuse rather than bake a bundle nobody can use.** `NEXT_PUBLIC_*` is inlined
# at build time, so a wrong value here is not a configuration mistake that can be
# corrected by restarting the container — it is a property of the image, and the
# only fix is another build. Failing here costs a minute; failing in a browser
# cost a deploy.
RUN test -n "${NEXT_PUBLIC_API_URL}" || (     echo "NEXT_PUBLIC_API_URL is empty. It is inlined into the browser bundle, so" >&2;     echo "an image built without it can never reach an API. Pass it:" >&2;     echo "  docker build --target prod --build-arg NEXT_PUBLIC_API_URL=https://... apps/web" >&2;     exit 1)

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
