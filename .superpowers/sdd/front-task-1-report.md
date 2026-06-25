# Front Task 1 Report: Scaffold Next.js + Vitest + proxy

## Status
COMPLETE — all gates passed.

## Commands Run & Results

1. `npm install` — succeeded, 559 packages added. Warnings: `next@15.1.3` has a CVE (CVE-2025-66478); version pinned per brief instructions (same major). `whatwg-encoding` deprecation warning (transitive dep, harmless).
2. `npm run test` — 1 passed (vitest v2.1.9, `lib/smoke.test.ts`)
3. `npm run build` — succeeded, Next.js 15.1.3, static output, no TS or lint errors.

## Resolved Dependency Version

- `@assistant-ui/react`: `^0.7.0` resolved to **`0.7.91`** (same major 0, same minor 7). Task 4 should target this version's API.

## Files Created

- `frontend/package.json`
- `frontend/next.config.mjs` — `/api/:path*` → `http://localhost:8000/:path*` rewrite
- `frontend/tsconfig.json`
- `frontend/vitest.config.ts`
- `frontend/app/globals.css`
- `frontend/app/layout.tsx`
- `frontend/app/page.tsx`
- `frontend/lib/smoke.test.ts`

`node_modules/` and `.next/` are NOT staged (covered by root `.gitignore`).

## Live Proxy Check

DEFERRED. The backend (uvicorn on :8000) was not running. The rewrite config in `next.config.mjs` is correct by inspection: `{ source: "/api/:path*", destination: "http://localhost:8000/:path*" }`. Live curl verification is deferred to Task 7 manual smoke test.

## Concerns

- `next@15.1.3` has a known CVE (CVE-2025-66478). The brief pinned this version explicitly; upgrading would require changing the major pin. Flagged for awareness — upgrade to a patched minor when the project spec allows.
- The CJS Vite Node API deprecation warning in `npm run test` output is cosmetic (Vitest internals), does not affect test results.
- 7 npm audit vulnerabilities (4 moderate, 1 high, 2 critical) mostly related to next@15.1.3. No action taken per brief (same major constraint).
