# ADR-086: v1.1 Research Workflow UX and Closed-Loop Productivity Architecture

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.1 architecture and Block 1

## Context

ECatVASP v0.1 through v1.0 established the scientific domain, Model Studio, VASP input/result
contracts, HPC execution, durable scientific workflow orchestration, electronic-structure analysis,
electrocatalysis thermochemistry, provenance/freshness/reporting, and a Windows-first production
desktop shell.

The post-merge-CI-verified v1.0 baseline is `main@dea7b542b26dc3371602cc2314d7543012a3bfac`.
v1.0 development is complete but unreleased: package version is `1.0.0.dev0`, project schema is v3,
and there is no tag, GitHub Release, or PyPI publication.

The next-version audit found that the scientific backend is substantially more capable than the
current desktop product surface. A scientist cannot yet complete the normal path

`model -> calculation -> workflow materialization -> HPC -> retrieval -> result -> promotion -> analysis -> thermochemistry -> reaction diagram`

through the desktop without lower-level Python use or manual handling of internal scientific ids and
evidence.

A previous planning branch proposed v1.1 as durable Study/sweep/ranking on schema v4. The Study
semantics are valid as a future design candidate, but implementing cohort-scale persistence before the
single-candidate desktop research loop is usable would scale an incomplete interaction model and add
migration, provenance, performance, and UI complexity prematurely.

ADR-085 also froze `ecatvasp-desktop-ipc-v1` as the final six-operation v1.0 contract. v1.1 therefore
must not silently reinterpret that completed boundary.

## Decision

The next development version is **v1.1 — Research Workflow UX & Closed-Loop Productivity**.

v1.1 productizes the existing scientific authorities as one task-oriented desktop research workflow.
It is not a Study/schema migration phase and not a new physical-method phase.

### 1. Schema v3 remains the durable scientific model

`SCHEMA_VERSION` remains 3.

v1.1 adds no required permanent top-level scientific entity. Existing authorities remain:

- `Project`;
- `Catalyst`;
- `StructureVariant`;
- `StructureSnapshot`;
- `ActiveSite`;
- `AdsorptionState`;
- `StateConformer`;
- `MethodFingerprint`;
- `ScientificWorkflowPlan`;
- `WorkflowStepBinding`;
- `Calculation`;
- `ExecutionAttempt`;
- `RemoteJob`;
- `Artifact`;
- `Analysis`;
- `ProvenanceRecord`;
- `DependencyRecord`.

No `Study`, persisted `Task`, `Session`, `WorkspaceState`, desktop command log, or frontend-owned
scientific entity is introduced.

If a later Block genuinely requires a new permanent scientific entity, scientific identity dimension,
or schema v4, that Block must stop for a new architecture decision.

### 2. v1.1 development version advances without a release

Block 1 moves development versions to:

- Python package `1.1.0.dev0`;
- desktop package/application `1.1.0-dev.0`.

Schema remains 3. No tag, GitHub Release, or PyPI publication is implied.

### 3. The product becomes task-oriented

The target desktop information architecture is:

- Home;
- Models;
- Calculations;
- Jobs;
- Results;
- Analysis;
- Reactions;
- Reports.

Advanced surfaces contain provenance, diagnostics, and project internals.

UUIDs, full SHA-256 values, workflow generations, artifact ids, and dependency ids remain available
for auditability but cease to be normal navigation or required manual input.

Task-oriented read models are transient projections only. They do not collapse the existing domain
state machines.

### 4. Lifecycle namespaces remain separate

The following separation is invariant:

`Calculation != ExecutionAttempt != RemoteJob`.

Scheduler completion never means scientific convergence. Result availability never means promotion
eligibility. A task-oriented UI may compose those facts, but Python remains authoritative for each
lifecycle namespace.

### 5. Existing Python authorities remain scientific truth

The desktop must not:

- read or mutate SQLite directly;
- calculate scientific hashes;
- infer MethodFingerprint identity;
- infer accepted structure lineage;
- evaluate workflow gates independently;
- classify VASP convergence;
- reconstruct atom identity geometrically;
- treat scheduler state as scientific success;
- implement scientific ranking or aggregation as hidden UI logic.

`ProjectStore`, domain constructors, workflow, VASP, execution, analysis, thermochemistry,
provenance, and freshness authorities remain in Python.

### 6. `ProjectApplicationService` remains the application seam

The existing application-service pattern is retained. User-intent wrappers may compose existing
scientific/application authorities, but they must not become a second scientific engine or second
workflow state machine.

Evidence-heavy operations such as workflow-step materialization, execution, VASP result analysis,
and structure promotion may later gain type-specific server-side evidence resolvers. Those resolvers
must reopen current ProjectStore state, verify exact managed artifacts/hashes, fail closed on stale or
foreign evidence, and return existing typed objects. They must not become a generic `resolve_anything`
layer.

### 7. Desktop IPC v1 remains frozen

`ecatvasp-desktop-ipc-v1` remains the exact v1.0 compatibility contract with six operations:

- `health`;
- `open_project`;
- `status`;
- `frontend_handoff`;
- `application_report`;
- `prepare_workflow`.

The v1 decoder/backend module is not extended with v1.1 operations.

### 8. v1.1 introduces `ecatvasp-desktop-ipc-v2`

Block 1 adds a separate v2 contract while the stdio host can dispatch both versions from the explicit
`protocol_version` field.

v2 preserves the successful transport properties of v1:

- local stdio/NDJSON framing;
- explicit request id and operation;
- explicit project root for project-scoped operations;
- strict unknown-field rejection;
- correlated response envelopes;
- stateless project requests;
- no implicit authoritative current-project session;
- no generic scientific mutation protocol.

v2 uses operation-specific request types rather than extending one flat request dataclass with every
future field.

Block 1 establishes the Python v2 contract and matching TypeScript contract fixtures. The production
Tauri/Svelte client remains on frozen v1 during this contract-only Block and switches to v2 when Block
2 introduces the first v2 Model Studio user operations. This avoids mixing a runtime-lifecycle change
into the protocol architecture Block while still making the sidecar v2-ready.

### 9. Block 1 adds one representative page-scoped read

v2 Block 1 adds `project_dashboard` as the representative task-oriented read operation.

The dashboard preserves separate calculation, analysis, execution-attempt, scheduler, and freshness
counts plus model/workflow counts. It deliberately does not build the full frontend handoff or all
StructureSnapshot presentations.

The frozen v1 `frontend_handoff` behavior remains unchanged for compatibility.

Future page-scoped reads may add pagination, filters, sorting, and on-demand scientific presentation.
Those query parameters are transient application state and never scientific identity.

### 10. Presentation becomes progressive disclosure

Default scientific surfaces prioritize human-facing scientific facts. Detailed scientific settings
remain available. Raw UUID/hash/provenance internals move primarily to Advanced/Inspector surfaces.

Provenance is not removed; it ceases to be navigation.

### 11. v1.1 roadmap

#### Block 1 — Research Task Architecture & Desktop IPC v2

Freeze the task-oriented information architecture, frozen-v1/v2 compatibility boundary,
operation-specific v2 request pattern, representative page-scoped dashboard read, version/schema
strategy, and test guards.

#### Block 2 — Project Creation & Interactive Model Studio

Expose project creation, import, graphene, vacancy/dopant, single/dual/triple metal sites, active-site
definition, adsorbate placement, conformers, and MatterViz-assisted selection using existing structure
authorities.

#### Block 3 — Calculation & Workflow Preparation Wizard

Expose scientific task selection, recipe/fingerprint construction, VASP setup/preflight, workflow
planning, exact step materialization, and accepted-structure handling without manual UUID/hash entry.

#### Block 4 — HPC Execution & Job Center

Expose materialization, staging, submission, monitoring, cancellation, retry/recovery, and retrieval
while preserving Calculation/ExecutionAttempt/RemoteJob separation.

#### Block 5 — Result Center & Structure Promotion

Expose managed VASP result intake/parsing, convergence classification, result summaries, exact
ExecutionPlan recovery, CONTCAR reconstruction, and explicit convergence-gated promotion.

#### Block 6 — Electronic Analysis Workspace

Expose real DOS/PDOS, Bader, charge-difference, COHP/ICOHP, and descriptor presentations while
keeping display transforms non-scientific.

#### Block 7 — Thermochemistry & Reaction Workspace

Expose thermochemistry, gas/reference management, CHE conditions, exact source binding,
HER/ORR/OER/CO2-to-CO evaluation, limiting metrics, and free-energy diagrams without manual energy
transcription.

#### Block 8 — Large-Project Performance & Product Hardening

Benchmark 10/100/1000-calculation projects and implement justified page-scoped/lazy read paths,
frontend virtualization, disposable caches, documentation repair, dependency reproducibility, and
accumulated product hardening. Schema v4 is not justified merely for pagination/performance.

#### Block 9 — Installed Desktop Scientific E2E Acceptance

Validate the installed Windows application through the representative closed-loop scientific path,
using production application/execution seams with controlled scheduler fixtures in CI, plus backend
restart, project switching, scientific drift, export, and packaging gates.

### 12. Representative v1.1 acceptance journey

The final target is:

`new project -> graphene/import -> vacancy/dopant/metal site -> active site -> OOH -> relax -> submit/monitor/retrieve -> parse/convergence -> promote -> downstream frequency/static/analysis -> thermochemistry -> ORR free-energy diagram -> report/export`.

The normal desktop path must not require:

- a Python shell;
- direct ProjectStore editing;
- manual transfer of UUID/hash values between screens;
- scheduler-success interpretation as convergence;
- manual copying of supported VASP energies into an external spreadsheet.

### 13. Global rejection gate

Any v1.1 Block must stop for a new architecture decision if it first requires:

1. a new permanent scientific entity;
2. a new scientific identity dimension;
3. `SCHEMA_VERSION = 4`;
4. a generic scientific mutation or dynamic RPC protocol;
5. a second workflow/task state machine;
6. scheduler success as scientific success;
7. frontend scientific inference;
8. durable Study/sweep/ranking/selection semantics;
9. a new physical method that changes existing method/thermochemistry identity semantics.

## Block 1 acceptance

Block 1 is accepted only if:

1. branch base is the post-merge-CI-verified v1.0 stable main;
2. Python and desktop versions enter v1.1 development state while schema remains 3;
3. the v1 protocol module retains its exact six-operation set and rejects v2 when invoked directly;
4. the stdio host explicitly dispatches both v1 and v2 without project/session authority;
5. v2 health advertises both supported protocol versions and the explicit v2 operation catalog;
6. v2 decoding uses operation-specific request types and strict unknown-field rejection;
7. no generic mutation operation exists;
8. `project_dashboard` is page-scoped and does not construct handoff/presentation payloads;
9. existing v1 sidecar/project-switch/scientific-drift tests remain green under the new package version;
10. TypeScript has a strict v2 contract/parser fixture while the production client remains v1 for this
    contract-only Block;
11. all existing scientific hashes/schema/provenance/freshness behavior remains unchanged;
12. Ruff, strict mypy, pytest on Python 3.11/3.12/3.13, MatterViz contract, desktop typecheck/tests/build,
    Rust/Tauri checks, and Windows packaging gates pass;
13. exact-head review/merge guards and exact-main post-merge CI pass.

## Non-scope

Block 1 does not implement project creation, model mutations, HPC submission, result analysis,
structure promotion, electronic-analysis commands, reaction commands, Study semantics, schema v4,
NEB, constant-potential DFT, microkinetics, ML screening, full Pourbaix, or public release state.

## Deferred direction

Durable Study/sweep/ranking remains a candidate for a later version after the single-candidate closed
loop is complete and 100-1000 calculation performance is measured. The earlier Study ADR candidate on
`plan/v11-study-screening-architecture` remains historical planning input and is not the v1.1
implementation authority.

## Consequences

v1.1 invests in converting already-built scientific capability into an operable research workbench.
The schema-v3 scientific model receives another full product cycle before migration. The frozen v1
contract remains reproducible, while a separate v2 boundary can grow type-specific task operations
without turning the desktop into a second scientific runtime.
