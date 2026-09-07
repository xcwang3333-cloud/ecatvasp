# ADR-077: v1.0 Production Desktop Workspace Architecture and Block 1 IPC Contract

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 architecture review and Block 1

## Context

ECatVASP v0.1 through v0.9 now provide a durable scientific core, execution/workflow layers,
electronic-structure and thermochemistry analyses, deterministic workspace/reporting projections,
a path-scoped Python facade, and `ecatvasp-frontend-handoff-v1`.

The post-merge-CI-verified v0.9 baseline is intentionally headless. ADR-008 selected
Tauri + Svelte/TypeScript with a local Python backend as the preferred desktop direction, while
ADR-075 established a versioned read-only frontend handoff and concluded that the handoff is
sufficient for a local process/RPC boundary. Production desktop packaging was deliberately deferred
until v1.0.

A second plausible v1.0 direction is durable high-throughput Study/sweep/ranking support. The current
repository has no Study entity. `ScientificWorkflowPlan` is an immutable DAG rooted at one exact
`StructureSnapshot`; it is not a cross-candidate research container. `ProjectBundle.from_entities()`
also rejects unsupported persisted entity types. A durable Study therefore introduces permanent
domain, schema, migration, provenance, freshness, and identity questions rather than merely another
workspace projection.

v1.0 must choose one primary risk axis instead of combining a new desktop runtime boundary with a new
durable scientific-study schema in the same phase.

## Architecture review

### Route A: Production Desktop Workspace

The v0.9 Python contract is sufficiently stable to begin production desktop work:

- `ProjectStore` remains the only durable project authority;
- `ProjectFacade` is path-scoped and reopens current state for every operation;
- `ProjectApplicationService` exposes typed application operations without owning scientific truth;
- workspace, inventory, readiness, presentation, report, and frontend-handoff contracts are already
  versioned or deterministic;
- `FrontendHandoff` is read-only and fail-closed against incompatible or stale scientific sources;
- MatterViz already has an explicit presentation-only adapter contract.

The missing pieces are product/runtime contracts: local IPC, process lifecycle, frontend shell,
project-open/switch UX, desktop-local preferences, runtime compatibility checks, packaging, and
platform CI. Those pieces can be added without changing scientific identity or `ProjectBundle`.

### Route B: Durable High-throughput Study / Sweep / Ranking

High-throughput work is scientifically valuable, but durable semantics are not a natural v0.9
projection extension.

A real Study needs at least:

- immutable study/design identity;
- exact candidate membership;
- sweep dimensions and concrete parameter assignments;
- links to per-candidate workflow/calculation identities;
- durable aggregation/ranking/selection semantics;
- provenance for derived summaries;
- freshness propagation from every scientific source used by an aggregate;
- migration/codec/store support for new permanent entities.

`ScientificWorkflowPlan` must not be overloaded for this purpose. It represents one root structure's
multi-Calculation DAG and its append-only materialization generations. A Study instead spans a cohort
of candidates and may own many workflow plans.

If durable Study is implemented later, the expected boundary is:

- `Study` (or equivalent immutable study-definition entity) becomes a top-level persisted entity;
- adding that entity requires a schema migration, so the expected next storage version is
  `SCHEMA_VERSION = 4`;
- a sweep value that changes a VASP scientific input must materialize a distinct existing
  Calculation/MethodFingerprint/recipe identity rather than being hidden as Study metadata;
- execution-only grouping/concurrency does not change Calculation scientific identity;
- ranking/aggregation that produces a scientific conclusion must be represented as a derived,
  parameterized provenance-bearing result whose identity includes criterion, direction, eligibility/
  filtering policy, normalization/tie semantics where applicable, and exact scientific source
  identities/hashes;
- purely organizational grouping labels may remain organizational metadata, but filtering or
  selection that changes the scientific result cannot be treated as display state;
- aggregate freshness must depend through `DependencyKind.SCIENTIFIC` on every source that
  contributed to the aggregate so source drift makes the aggregate stale;
- membership or scientific selection-policy changes require a new durable derived identity rather
  than silently updating an old ranking.

Those semantics deserve a dedicated schema-v4 architecture phase instead of being mixed into desktop
productization.

### Route C: staged hybrid

The selected hybrid is temporal rather than architectural mixing:

1. v1.0 delivers Production Desktop Workspace on schema v3.
2. v1.0 may offer transient comparison/filter/sort views over existing workspace/report DTOs, but it
   must not persist them as a Study or claim durable ranking provenance.
3. A later version performs an explicit durable Study architecture review and, if approved, moves to
   schema v4.

This keeps the desktop client ready to present future Study contracts without forcing their scientific
semantics into v1.0.

## Decision

v1.0 is **Production Desktop Workspace**.

Durable Study/sweep/ranking is deferred to a later schema-focused phase. NEB, solvation,
constant-potential/grand-canonical DFT, microkinetics, ML, full Pourbaix, and other physical-method
extensions remain deferred as well.

### 1. Scientific and storage authority remain in Python

The desktop shell is an application/presentation client.

It must not:

- read or mutate SQLite/project internals directly;
- become a scientific hash/freshness authority;
- own workflow history or scheduler/scientific convergence state;
- infer scientific identity from filenames, labels, geometry, or UI state;
- persist frontend state into `ProjectBundle`.

All scientific mutations continue through typed Python authorities.

### 2. Add a versioned local IPC contract

Block 1 adds:

`ecatvasp-desktop-ipc-v1`

The initial contract is deliberately narrow and read-only:

- `health`;
- `open_project`;
- `status`;
- `frontend_handoff`.

Requests carry an explicit `project_root` for project-scoped operations. The backend does not retain an
authoritative "current project", cached `ProjectBundle`, workspace projection, or session state between
requests.

The wire representation is strict JSON suitable for newline-delimited local process framing. Unknown
protocol versions, operations, or fields fail closed.

Later blocks may add typed mutation operations, but v1.0 must not introduce a generic arbitrary JSON
mutation protocol for scientific entities.

### 3. IPC identity is non-scientific

`request_id`, operation name, transport framing, backend process identity, and desktop error codes are
application/runtime concerns.

They are not:

- scientific identity;
- `Analysis.parameters_hash`;
- provenance subjects;
- dependency hashes;
- workflow generation identity;
- freshness inputs.

### 4. Backend health and project-open are validation operations

`health` advertises backend/package and frontend-handoff contract compatibility without opening a
project.

`open_project` validates the exact current `ProjectStore` and returns project identity/schema metadata.
It does not create a persistent desktop session. Subsequent project-scoped calls still carry the
explicit project path and reopen current state.

This makes project switching and backend restart safe: no cached session can override durable state.

### 5. Desktop-local UX state remains outside ProjectBundle

The following are explicitly transient or desktop-local state:

- current project path and recent-project list;
- window geometry and panel layout;
- selected tab/entity/row;
- filters, sorting, table column layout, and search text;
- MatterViz camera/view controls;
- plot axis ranges, colors, line styles, visibility, and other style;
- theme and accessibility preferences;
- pending form drafts before an explicit typed application action;
- frontend caches;
- backend process id, request ids, progress indicators, notifications, and crash/restart metadata.

If retained across application restarts, such state belongs in operating-system application
preferences/cache storage, not the scientific project bundle. It is never provenance.

### 6. Runtime and packaging boundary

Tauri + Svelte/TypeScript remains the selected desktop shell direction. The Python scientific package
must remain independently usable by CLI/tests/API clients.

Desktop build/runtime dependencies belong to a desktop-specific subtree/build pipeline. Tauri/Rust,
Node/Svelte/TypeScript, and MatterViz do not become Python scientific runtime dependencies.

The local Python backend will be launched as a desktop sidecar/process. The versioned JSON contract
allows the executable-packaging mechanism to change without changing scientific/storage contracts.
Exact bundling/installer mechanics are benchmarked and frozen in a later v1.0 block.

### 7. Windows-first is the initial production target

Windows-first is accepted as the first packaging target because the desktop shell is decoupled from
where VASP executes and the existing execution layer already treats scheduler/HPC work separately
from application presentation.

Windows-first does not mean Windows-only. The architecture remains portable; Linux/macOS packaging is
deferred until the Windows pipeline and local-backend lifecycle are stable.

The current Ubuntu-only Python CI is insufficient for desktop delivery, so later v1.0 blocks must add
Windows desktop build/smoke coverage and path/process-lifecycle tests.

### 8. Schema, scientific identity, provenance, and freshness remain frozen in v1.0

The selected desktop phase adds no permanent scientific entity and no migration.

- `SCHEMA_VERSION` remains 3;
- existing `scientific_hash()` contracts remain unchanged;
- existing `DependencyKind.SCIENTIFIC` propagation remains authoritative;
- report/presentation/handoff/IPC identities remain non-scientific;
- package version enters `1.0.0.dev0`.

Any later v1.0 proposal that needs a permanent entity or scientific identity dimension must stop for a
new architecture decision rather than being smuggled in as UX state.

## v1.0 roadmap

### Block 1 — Desktop backend IPC contract

Objective:
Establish a strict, versioned, stateless local-backend seam over existing v0.9 contracts.

Boundary:
Read-only transport adapter only; no Tauri shell, no background daemon state, no scientific mutation.

Modules/files:
`src/ecatvasp/desktop/`, package version files, ADR-077, desktop IPC tests.

Persistence/schema:
None; schema remains v3.

Provenance/freshness:
None added; frontend handoff continues to consume existing authorities.

Dependencies:
Standard library plus existing ECatVASP package only.

Tests/acceptance:
Strict codec/version/operation validation, health handshake, project-open validation, status/handoff
reopen behavior, project switching without session leakage, missing-project fail-closed behavior,
version/schema freeze, full existing CI matrix.

Risks:
Accidentally turning a project handle/session or request metadata into authority; genericizing scientific
mutations too early.

### Block 2 — Local backend host and process lifecycle

Objective:
Provide the executable local sidecar entry point and deterministic startup/health/shutdown behavior.

Boundary:
Process orchestration only; each project request still resolves current `ProjectStore` state.

Modules/files:
Desktop host/stdio framing, process protocol tests, CLI/module sidecar entry point, ADR-078.

Persistence/schema:
None.

Provenance/freshness:
None.

Dependencies:
Prefer standard library on Python side; no web server or database server.

Tests/acceptance:
NDJSON request/response framing, malformed-message isolation, health before project open, graceful
shutdown, EOF/crash behavior, restart without state loss, no stdout protocol contamination.

Risks:
Deadlock/framing bugs and platform-specific subprocess behavior.

### Block 3 — Tauri + Svelte/TypeScript shell and typed contract client

Objective:
Create the production desktop shell and consume the Python IPC/frontend contracts without direct
storage access.

Boundary:
Frontend is presentation/application only; no scientific logic in TypeScript.

Modules/files:
`ui/desktop/`, Tauri config/Rust shell, Svelte/TypeScript client types, contract fixtures, ADR-079.

Persistence/schema:
No ProjectBundle impact.

Provenance/freshness:
No new authority; client displays exact backend freshness and source hashes.

Dependencies:
Desktop-only Tauri/Rust/Node/Svelte/TypeScript/MatterViz build dependencies.

Tests/acceptance:
Frontend typecheck/build, backend compatibility handshake, unknown major contract rejection, smoke
launch, existing Python/MatterViz CI remains green.

Risks:
Contract duplication drift and frontend-owned fallback logic.

### Block 4 — Project lifecycle UX and desktop-local preferences

Objective:
Implement open/switch/recent-project flows and safe local UX persistence.

Boundary:
Project paths/preferences are desktop-local; project scientific state remains in ProjectStore.

Modules/files:
Desktop project service/client state, OS app-config adapter, path/error UX, ADR-080.

Persistence/schema:
Only OS-local app preferences/cache; never ProjectBundle; schema remains v3.

Provenance/freshness:
None.

Dependencies:
Existing desktop stack.

Tests/acceptance:
Open valid/invalid projects, switch A->B->A without cross-project leakage, recent-list persistence,
backend restart, project files unchanged by preference updates.

Risks:
Path canonicalization/Windows path edge cases and accidental session authority.

### Block 5 — Workspace, provenance, readiness, and scientific presentation views

Objective:
Render the existing v0.9 workspace as a usable research desktop including MatterViz and scientific
presentation DTOs.

Boundary:
Consume handoff/report/presentation contracts exactly; display transforms remain non-scientific.

Modules/files:
Desktop workspace/inventory/readiness views, MatterViz components, presentation adapters, ADR-081.

Persistence/schema:
None.

Provenance/freshness:
Display existing ids, dependency kinds, source hashes, stale/blocked reasons; no recomputation.

Dependencies:
Existing desktop stack and locked MatterViz compatibility.

Tests/acceptance:
Lifecycle namespaces remain separate, stale/blocked reasons visible, old source-hash presentations
rejected/refreshed, atom identity preserved, no filename inference.

Risks:
UI simplification accidentally hiding scientific blockers or changing sign/axis semantics.

### Block 6 — Typed desktop application actions

Objective:
Expose selected safe application operations through versioned IPC with explicit receipts.

Boundary:
Each command delegates to `ProjectApplicationService` or another existing type-specific authority.
No generic scientific mutation JSON API and no scheduler-success shortcut.

Modules/files:
Desktop typed command DTOs/dispatch, API adapters, frontend action forms, ADR-082.

Persistence/schema:
Only existing domain mutations through ProjectStore; no desktop entity.

Provenance/freshness:
Exactly existing lower-layer records and gates.

Dependencies:
No new Python scientific runtime dependency.

Tests/acceptance:
Representative prepare/report and selected existing operations round-trip through IPC; stale inputs,
unconverged promotion, foreign ids, and obsolete workflow generations fail closed; receipts match the
underlying application service.

Risks:
Flattening type-specific scientific contracts into overly generic UI payloads.

### Block 7 — Backend bundling, runtime compatibility, and Windows packaging pipeline

Objective:
Freeze a reproducible local-Python sidecar packaging strategy and Windows-first desktop build.

Boundary:
Packaging may bundle Python but cannot alter Python scientific behavior or project schema.

Modules/files:
Desktop build scripts/config, sidecar packaging, Windows CI workflow additions, ADR-083.

Persistence/schema:
None.

Provenance/freshness:
None.

Dependencies:
Build/distribution dependencies only; document exact versions and separation from Python runtime deps.

Tests/acceptance:
Clean Windows CI build, packaged backend health handshake, package-version compatibility, project-open
smoke test, no network listener required for local control plane.

Risks:
Binary size, antivirus/signing behavior, native dependency compatibility, Python resource discovery.

### Block 8 — Desktop resilience, security, diagnostics, and export integration

Objective:
Harden failure handling, logs, restart/recovery, safe paths, deterministic reports/exports, and
diagnostic surfaces.

Boundary:
Diagnostics are runtime observations, not scheduler/scientific facts or provenance.

Modules/files:
Desktop diagnostics/error mapping, log routing, export UI, security/path guards, ADR-084.

Persistence/schema:
Logs/preferences outside ProjectBundle; schema unchanged.

Provenance/freshness:
Reports continue to carry existing provenance/freshness; diagnostics do not override them.

Dependencies:
No new scientific dependency.

Tests/acceptance:
Backend crash/restart, corrupt/missing project, incompatible contract, stale source, export
determinism, safe error handling, no credentials/scientific secrets leaked into protocol diagnostics.

Risks:
Over-broad exception handling, path exposure, or misleading "recovered" UI state.

### Block 9 — Final desktop E2E acceptance and hardening

Objective:
Prove the desktop, backend, ProjectStore, scientific presentation, and fail-closed drift behavior as
one production workspace.

Boundary:
Acceptance may repair owning layers only; it may not add new scientific methods or Study semantics.

Modules/files:
Cross-layer desktop E2E harness/fixtures, packaging smoke CI, ADR-085.

Persistence/schema:
Schema v3 remains frozen; only existing project entities may be persisted.

Provenance/freshness:
Existing provenance/freshness remains the sole authority.

Dependencies:
Frozen v1.0 desktop/runtime stack.

Tests/acceptance:
Full final E2E contract below plus Ruff, strict mypy, pytest on 3.11/3.12/3.13, MatterViz, desktop
typecheck/build, and Windows packaging smoke.

Risks:
Late cross-platform/process failures or UI behavior that masks stale/blocked scientific state.

## Final v1.0 E2E acceptance contract

v1.0 is complete only when CI proves that a production desktop build can:

1. start the local Python backend and complete an exact protocol/package compatibility health
   handshake;
2. open a persisted schema-v3 `ProjectStore` without the frontend reading storage internals;
3. switch between at least two projects and restart the backend without cached/session state becoming
   authoritative;
4. display workspace inventory, Calculation scientific status, Analysis status, ExecutionAttempt
   status, Scheduler state, workflow readiness, and freshness as separate namespaces;
5. render an exact identity-preserving MatterViz structure presentation and representative existing
   electronic/reaction presentation data through the v0.9 contracts;
6. generate/display the deterministic scientific report/frontend handoff with identifiers,
   provenance, dependencies, units, conditions, contract versions, and stale/blocked reasons intact;
7. execute representative typed application actions through Python authorities and return exact
   receipts without a generic mutation engine;
8. mutate persisted scientific content while retaining permanent entity UUID and, after refresh or
   backend restart, observe downstream `DependencyKind.SCIENTIFIC` staleness and reject the old
   presentation by source-hash mismatch;
9. prove rebuilding a current presentation cannot hide stale downstream scientific state;
10. persist recent-project/window/filter/camera/style preferences only outside ProjectBundle and prove
    those changes do not alter schema, scientific identity, provenance, or project hashes;
11. build and smoke-test the Windows-first packaged desktop/backend path; and
12. keep package `1.0.0.dev0`, `SCHEMA_VERSION = 3`, no tag, no GitHub Release, and no PyPI
    publication until a separate release decision is made.

## Deferred beyond v1.0

The following remain outside v1.0 unless separately approved by a new architecture decision:

- durable high-throughput Study/sweep/candidate/ranking/selection entities and schema v4;
- NEB, transition-state, and barrier workflows;
- implicit or explicit solvation models;
- electric-field corrections that change scientific identity;
- charged-cell, constant-potential, or grand-canonical DFT;
- microkinetics and kinetic parameter fitting;
- ML training/inference or surrogate screening;
- full Pourbaix engines;
- other physical-method extensions that introduce new scientific identity dimensions;
- multi-user/server collaboration infrastructure;
- Linux/macOS production packaging parity if it would compromise the Windows-first v1.0 acceptance
  scope.

## Consequences

v1.0 converts the mature headless workbench into a production desktop product while preserving the
frozen scientific core. Durable Study semantics remain a clean future schema/domain project instead of
being entangled with GUI process, packaging, and platform risk.
