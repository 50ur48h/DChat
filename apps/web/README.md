# apps/web — data-agent web app

Next.js (App Router) + TypeScript. Deliberately thin: all intelligence and all
security live server-side (architecture Part 3.1). The browser holds no secret
and enforces no rule.

## Layout

```
src/
  app/                 routes; `/` is the Phase 0 health page
  components/          UI components (co-located CSS Modules)
  lib/api-client/      typed fetch helper for the API
```

`lib/auth/` (MSAL) arrives in Phase 2; the conversation, trace and catalog
screens arrive in Phases 7–11.

## Local development

```bash
cp .env.example .env.local     # NEXT_PUBLIC_API_URL
pnpm install
pnpm dev                       # http://localhost:3000
```

The health page calls `GET ${NEXT_PUBLIC_API_URL}/healthz` from the browser, so
the API must be running (`make api.dev`) and must allow this origin — it does by
default via `CORS_ORIGINS`.

| Command | What it does |
|---|---|
| `pnpm lint` | eslint (`eslint-config-next`, core-web-vitals + typescript) |
| `pnpm typecheck` | `next typegen` then `tsc --noEmit` |
| `pnpm test` | vitest + Testing Library, jsdom |
| `pnpm build` | production build (standalone output) |

`next typegen` runs before `tsc` because the App Router's generated route types
(`LayoutProps`, `PageProps`) do not exist on a clean checkout.

## The API client

Hand-written in `lib/api-client/` for now, and it **validates what it receives**
rather than casting — a lying type is worse than no type. From Phase 7 these
types are generated from the API's OpenAPI schema so contract drift breaks CI
instead of production (backlog **B-003**).

## Container

```bash
docker build --target prod \
  --build-arg NEXT_PUBLIC_API_URL=https://api.example.com -t dataagent-web:local apps/web
```

`NEXT_PUBLIC_*` values are inlined into the browser bundle at **build** time, so
the API URL is a build argument — setting it at runtime has no effect. Never put
a secret behind that prefix.

## Fonts

`next/font/google` is deliberately not used: it downloads font files during
`next build`, which would make `docker build` and CI depend on network access to
a third-party CDN. The app uses a system font stack instead.
