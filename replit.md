# NeuroRoute-SDN

Windows-first SDN research simulator with a SimPy/FastAPI backend and a live React dashboard for topologies, queue metrics, controller state, and experiments.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the shared API proxy
- `pnpm --filter @workspace/neuroroute-sdn run dev` — run the NeuroRoute dashboard
- `py -3.11 -m uvicorn neuroroute_api.main:app --app-dir artifacts/neuroroute-sdn/backend --port 8000` — run the native Python API from the workspace root
- `pwsh -File artifacts/neuroroute-sdn/scripts/run_all.ps1` — run the built-in experiment suite and export CSV/SVG charts
- `pnpm run build` — build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec (Orval writes TypeScript into `lib/api-client-react/src/generated` and `lib/api-zod/src/generated`)
- Required env: none for the local simulator; runtime data is stored in SQLite

## Stack

- pnpm workspaces, Node.js 24, JavaScript (ESM); hand-written source is plain JS/JSX — the `@workspace/api-client-react` and `@workspace/api-zod` packages are Orval-generated TypeScript (including their `src/index.ts` entry, which codegen rewrites) and Vite/esbuild compile those natively
- API: Python 3.11 FastAPI; Express 5 proxies hosted preview requests
- DB: SQLite + SQLAlchemy for simulator experiments and queue samples
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `artifacts/neuroroute-sdn/backend/neuroroute_api` — Python API, simulator, storage, controller, topology, and optional research models
- `artifacts/neuroroute-sdn/src` — React dashboard
- `lib/api-spec/openapi.yaml` — REST API contract
- `artifacts/neuroroute-sdn/backend/data` — local SQLite data (ignored by Git)

## Architecture decisions

- The native Python FastAPI backend is the product's primary runtime. The shared Express API process only proxies `/api` and WebSocket traffic to it in the hosted preview.
- The default SimPy backend is self-contained; Mininet/Ryu is an optional WSL2 adapter, not a runtime requirement.
- Research-paper-specific equations and Algorithm 1 are not asserted until the source paper is supplied.

## Product

The dashboard can start and control SimPy runs, display topology and queue metrics, inspect simulated controller state, install queue-rate/flow-table changes, and compare saved experiments.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure and package details
