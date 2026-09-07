# ADR-079: v1.0 Tauri + Svelte Desktop Shell and Typed Backend Client

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 Block 3

## Context

ADR-077 selected a Production Desktop Workspace for v1.0 and reserved Block 3 for the first Tauri +
Svelte/TypeScript shell. ADR-078 then froze the local Python backend as a silent stdio NDJSON sidecar:
`health` is the explicit readiness handshake, EOF is canonical graceful shutdown, malformed frames are
isolated at the host boundary, and every project-scoped Python request remains stateless and carries an
explicit project path.

Block 3 must connect a production desktop runtime to that backend without allowing the browser layer to
become a second scientific application implementation. In particular, the frontend must not read
ProjectStore files, infer scientific semantics, cache a ProjectBundle, reinterpret freshness, or create
fallback behavior when a backend contract is incompatible.

The packaging mechanism for the Python executable is deliberately still deferred to Block 7. Therefore
this block needs a development/runtime process seam that can later point to a bundled sidecar without
changing the Python IPC or TypeScript contracts.

## Decision

### 1. Tauri owns process lifecycle; Svelte owns presentation state

The desktop runtime is split into two non-scientific layers:

- Rust/Tauri owns the local Python child process, stdin/stdout exchange, health gating, request/response
  correlation, and process shutdown;
- Svelte/TypeScript owns visible connection state and typed read-only request construction.

Neither layer owns project scientific state.

The Rust `BackendState` may retain only runtime/process information: child process handles, the stdio
transport, readiness state, and request sequencing for Tauri-generated health requests. It does not
retain the current project, ProjectBundle, workflow state, Calculation state, Analysis state, freshness
facts, or scientific results.

### 2. The Python contract remains the authoritative wire contract

The authoritative backend application contract remains:

`ecatvasp-desktop-ipc-v1`

The authoritative frontend handoff contract remains:

`ecatvasp-frontend-handoff-v1`

The TypeScript client mirrors only the transport DTO shape needed to consume these contracts. Contract
fixtures are checked against the live Python implementation in Python tests so a backend change cannot
silently drift away from the frontend fixture.

Scientific report content inside the frontend handoff remains an opaque JSON object in Block 3. Full
scientific presentation typing and rendering belongs to Block 5. This prevents TypeScript from
prematurely duplicating backend scientific schemas or interpretation rules.

### 3. Health is mandatory before project-scoped exchange

Tauri exposes three application commands to the webview:

- `backend_health`
- `backend_exchange`
- `backend_shutdown`

`backend_health` starts the Python sidecar when needed, sends the exact Python `health` operation, checks
request/response correlation, and rejects incompatible contracts before marking the process ready.

Compatibility requires all of the following:

- exact `ecatvasp-desktop-ipc-v1` protocol version;
- backend package major version 1;
- exact `ecatvasp-frontend-handoff-v1` handoff version;
- `stateless_project_requests = true`;
- all Block 3 read operations advertised by the backend.

The TypeScript client repeats the compatibility check before exposing a ready UI state. It does not
fallback to a previous contract, omit fields, or reinterpret an incompatible response.

### 4. Block 3 keeps the Tauri exchange surface read-only

After health succeeds, Rust accepts only the existing Block 1 read-only project operations:

- `open_project`
- `status`
- `frontend_handoff`

A `health` request from the frontend does not pass through the generic exchange path; it uses the
explicit health command. Unknown and future operation names are rejected by the Rust boundary in Block
3 even if a later Python package were to advertise them.

This is not a scientific authorization mechanism. It is a phase-boundary guard that prevents Block 3
from accidentally exposing future mutation APIs before Block 6 freezes typed application actions.

### 5. Request/response correlation is checked on both sides of the boundary

The TypeScript client creates deterministic per-client request ids for project calls. Rust canonicalizes
and forwards one JSON object as one NDJSON line, then validates that the response has:

- the exact IPC contract version;
- the same request id;
- the same operation;
- a valid success/failure flag.

The TypeScript parser checks the same correlation before returning a typed success response.

No response is matched by ordering alone.

### 6. Process shutdown remains transport lifecycle, not a Python application operation

ADR-078 froze EOF as canonical graceful shutdown. Block 3 preserves that rule.

`backend_shutdown` is a Tauri process-management command. It closes the sidecar stdin and waits for the
child process to exit. It does not add `shutdown` to `DesktopOperation` and therefore does not mutate the
Python application contract.

A Rust drop fallback may terminate a still-running child during abnormal desktop teardown. This is a
runtime cleanup fallback only; resilience/restart policy is deferred to Block 8.

### 7. Development executable resolution is replaceable wiring

Until Block 7 freezes sidecar bundling, Tauri resolves the backend executable as:

1. `ECATVASP_DESKTOP_BACKEND` when explicitly set for development/test wiring;
2. otherwise `ecatvasp-desktop-backend` from the process PATH.

The environment value is an executable path only; Block 3 accepts no arbitrary command argument string.
This setting is runtime wiring, not project state, scientific identity, provenance, or a desktop user
preference.

Block 7 may replace this resolver with a packaged-sidecar location without changing the IPC contract.

### 8. Frontend dependencies stay in `ui/desktop`

The desktop subtree uses the current stable v1.0 development stack selected on 2026-09-07:

- Tauri core 2.11.5;
- Tauri JavaScript API 2.11.1;
- Tauri Node CLI 2.11.4;
- Tauri build crate 2.6.3;
- Svelte 5.57.0;
- Svelte Vite plugin 7.3.0;
- Vite 8.1.0;
- official `svelte-check` 4.7.6;
- TypeScript 6.0.3, the latest stable 6.x line compatible with `svelte-check`'s declared peer range;
- Vitest 5.0.0;
- Node 24 in CI.

These are desktop build/runtime dependencies only. `pyproject.toml` and Python scientific runtime
dependencies do not change.

Exact lockfile/distribution reproducibility is finalized with the packaging pipeline in Block 7. Block
3 pins direct desktop package versions and verifies installation/build from the declared manifest.

### 9. Block 3 shell is intentionally minimal

The visible Svelte shell displays only backend connection/compatibility information. It does not yet
implement:

- recent projects or current-project persistence;
- file dialogs or project switching UX;
- workspace inventory;
- provenance/freshness dashboards;
- MatterViz scientific rendering;
- report/export workflows;
- application mutation forms.

Those belong to Blocks 4 through 8. Keeping the first shell small makes the runtime contract testable
without coupling it to unfinished product workflows.

### 10. No schema, scientific identity, provenance, or freshness change

Block 3 adds no persisted ECatVASP entity and no migration.

- `SCHEMA_VERSION` remains 3;
- package version remains `1.0.0.dev0` on the Python side;
- all ProjectStore authority remains in Python;
- scientific hashes remain unchanged;
- `DependencyKind.SCIENTIFIC` remains the only scientific freshness propagation authority;
- Tauri process state and Svelte connection state are non-scientific runtime state.

## CI and acceptance contract

Block 3 is accepted only when the exact PR head passes all previous CI plus a desktop-shell job that:

1. installs the Linux dependencies required by the current Tauri v2 WebKitGTK 4.1 toolchain;
2. uses Node 24 and a current stable Rust toolchain;
3. installs `ui/desktop` dependencies from its manifest;
4. passes official `svelte-check`;
5. passes Vitest contract tests;
6. builds the Vite/Svelte frontend;
7. passes Rust unit tests for operation gating and response correlation;
8. passes `cargo check` for the Tauri crate.

The existing Python quality job, Python 3.11/3.12/3.13 matrix, and MatterViz contract job must remain
green.

A head change invalidates previous evidence. Final merge still requires exact-head CI, mergeability,
no main drift, `behind_by = 0`, no blocking review or unresolved thread, squash merge, and exact-main
post-merge push CI.

## Consequences

The project now has a real desktop runtime boundary rather than only a theoretical frontend handoff.
The browser layer cannot directly reach ProjectStore and cannot proceed when backend compatibility is
unknown. The same Python sidecar contract can later be packaged without changing scientific storage or
presentation contracts.

The main remaining risk is cross-language contract duplication. The mitigation is deliberately
layered: Python-owned fixtures, Rust runtime validation, TypeScript runtime validation, and exact-head CI.
The frontend should not expand its mirror of scientific report DTOs until Block 5 has a concrete view
that needs them.

## Deferred

- project open/switch/recent UX and OS-local preferences: Block 4;
- workspace/provenance/readiness/MatterViz views: Block 5;
- typed scientific/application mutations: Block 6;
- bundled Python sidecar discovery, lockfile/distribution freeze, Windows packaging: Block 7;
- crash restart policy, diagnostics, security hardening, export integration: Block 8;
- final packaged desktop E2E acceptance: Block 9.
