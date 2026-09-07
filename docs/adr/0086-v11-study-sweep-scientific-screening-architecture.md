# ADR-086: v1.1 Durable Study, Sweep, and Scientific Screening Architecture

- Status: Proposed
- Date: 2026-09-07
- Scope: next-version architecture planning after v1.0 development freeze

## Context

ECatVASP v1.0 Production Desktop Workspace is development-complete on post-merge-CI-verified
`main`. Release state intentionally remains unchanged: package version is still `1.0.0.dev0`, project
schema remains v3, and no GitHub Release or PyPI publication has been created.

ADR-077 deliberately deferred durable Study/sweep/ranking support because `ScientificWorkflowPlan`
models one exact root `StructureSnapshot` and one immutable multi-Calculation DAG. It is not a
cross-candidate research container. The current `ProjectBundle` also rejects unsupported persisted
entity types, so a durable Study cannot be added as a workspace-only projection.

The deferred Study direction is now the preferred next scientific-product risk axis. NEB/transition
states, solvation, charged/constant-potential DFT, microkinetics, ML/surrogate screening, full
Pourbaix, multi-user collaboration, and Linux/macOS production packaging parity remain separate
future decisions.

## Decision

The next development version is planned as **v1.1 — Durable Study, Sweep, and Scientific Screening
Core**.

v1.1 introduces a schema-v4 scientific-study layer above the existing per-Calculation and
per-ScientificWorkflowPlan authorities. It does not replace or weaken any v1.0 scientific identity,
provenance, freshness, workflow, scheduler, or desktop boundary.

The planning branch itself does not bump package or schema versions. The package moves to
`1.1.0.dev0` and `SCHEMA_VERSION` moves from 3 to 4 only when v1.1 Block 1 implementation begins on an
independent feature branch after this architecture is accepted.

### 1. Study is a new immutable top-level scientific design entity

`Study` is a durable project entity representing one exact cross-candidate research design.

It is not:

- a `ScientificWorkflowPlan`;
- a scheduler job group;
- a desktop saved filter/sort/search;
- a mutable spreadsheet-like table;
- a generic bag of arbitrary JSON parameters.

A Study may span many root `StructureSnapshot`s, many MethodFingerprints, many workflow plans, and many
Calculations.

Its scientific content includes at minimum:

- project identity;
- exact candidate membership;
- exact typed sweep/design axes;
- exact resolved scientific assignments for every member;
- canonical workflow-recipe identity where a workflow is part of the design;
- any scientific eligibility/comparability policy that changes which members are considered part of
  the scientific design.

Display title, notes, UI order, table column layout, colors, and other presentation metadata do not
change Study scientific identity.

Study membership is set-like scientific content. Member and axis order is canonicalized so merely
reordering the UI cannot create a new scientific identity. Duplicate exact members are rejected.
Changing membership or a result-changing Study design policy requires a new durable Study identity;
it must not silently rewrite an existing scientific Study.

### 2. Study members resolve to existing scientific authorities

A Study does not create a second calculation-identity system.

Every concrete member resolves to existing scientific identities, including as applicable:

- an exact `StructureSnapshot` and its scientific hash;
- an exact `MethodFingerprint`;
- a canonical workflow recipe identity;
- concrete Calculation identities produced by existing VASP/workflow materialization authorities.

A sweep value that changes Method, Protocol, Recipe, structure input, k-point policy, or another
scientific input must therefore produce a distinct existing scientific identity before or during Study
materialization.

Execution-only settings such as nodes, cores, partition, walltime, `NCORE`, `KPAR`, retry grouping,
and concurrency limits remain execution/application policy. They do not enter Study scientific
identity and must not create a new Calculation merely because execution tuning changed.

### 3. Sweep planning is typed and resolves before persistence authority

v1.1 may provide ergonomic sweep planning, but it must not expose arbitrary INCAR paths or a generic
scientific mutation dictionary.

A typed sweep planner may accept explicitly supported axes such as:

- exact structure-snapshot cohort membership;
- exact MethodFingerprint variants;
- canonical workflow-recipe variants;
- type-specific Method/Protocol/Recipe parameter variants that are constructed through existing
  domain constructors and VASP resolution rules.

The planner expands the requested design and validates every combination through the existing
scientific authorities. The durable Study records the typed design intent plus exact resolved member
identities/hashes. Invalid cross-layer assignments fail closed.

No Study field may hide a scientific-input change that should have appeared in MethodFingerprint,
RecipeIdentity, Calculation, or another existing identity contract.

### 4. Study materialization does not overload ScientificWorkflowPlan

`ScientificWorkflowPlan` remains one-root DAG intent with append-only workflow-step generations.

Study materialization may create or reuse one workflow plan per candidate as appropriate, but the
Study itself remains the cohort-level owner of membership/design semantics. A Study-to-workflow
materialization/binding layer may record exact candidate-to-plan relationships, but it is orchestration
metadata and cannot replace the Study scientific design or a Calculation scientific identity.

Historical workflow generations remain authoritative. Study reconciliation may project member
readiness/status, but it must never infer a missing historical workflow generation from the current
one.

### 5. Study is provenance-aware and source drift must be observable

Because a Study design can depend on exact scientific sources, Study materialization records exact
source hashes and uses existing provenance/freshness machinery rather than inventing Study-local
freshness rules.

Where Study scientific meaning depends on a source entity, dependencies use
`DependencyKind.SCIENTIFIC`.

If a same-UUID source changes scientific hash:

- the affected Study/member state becomes stale or blocked according to the existing freshness model;
- downstream Study aggregates become stale transitively;
- rebuilding a current desktop/table presentation cannot hide the stale scientific state.

### 6. Ranking is a durable derived scientific result, not a table sort

A scientific ranking cannot be represented as desktop sort order.

v1.1 introduces a type-specific durable ranking result, provisionally `StudyRanking`, whose scientific
identity/parameters include at minimum:

- exact Study identity/hash;
- metric/criterion identity;
- optimization direction;
- eligibility/filter policy;
- missing/invalid/stale-data policy;
- scientific comparability policy;
- normalization policy, if any;
- tie semantics;
- exact source entity identities and recorded scientific hashes;
- units and physical/electrochemical conditions needed to interpret the metric.

Every scientific source contributing to a ranking is connected by `DependencyKind.SCIENTIFIC`.
Any contributing source drift therefore makes the old ranking stale.

The default ranking semantics must preserve scientific ties. UI labels, insertion order, UUID text,
or alphabetical order must not be used as an undeclared scientific tie-breaker.

### 7. Comparability and eligibility fail closed

High-throughput aggregation must not make incomparable calculations look commensurate merely because
they expose scalar values.

Before ranking, an explicit comparability gate checks the scientific contracts relevant to the metric,
including MethodFingerprint layers and metric conditions where applicable. The initial policy should
be conservative; incompatible core methods/protocols or incompatible thermochemical/electrochemical
conditions are blocked unless a later explicit scientific policy permits the comparison.

Stale, invalid, unconverged, missing, or otherwise ineligible candidates are never silently dropped.
The eligibility/missing-data policy is explicit and result-changing policy is part of ranking identity.

### 8. Scientific screening selection is separate from ranking

A user visually selecting rows is desktop state. A scientific selection that determines downstream
work is not.

When a ranking or threshold/top-k rule is used to choose candidates for additional scientific work,
v1.1 represents that decision as a type-specific durable derived entity, provisionally
`StudySelection`.

Its identity includes:

- source Study and ranking identity/hash;
- selection rule and thresholds/top-k semantics;
- eligibility and tie-boundary behavior;
- exact selected member identities.

`StudySelection` depends scientifically on the ranking and therefore becomes stale if the ranking or
any contributing scientific source drifts. Downstream workflows created from a scientific selection
must reference that exact accepted selection; the desktop must not reconstruct it from current row
selection state.

### 9. Schema v4 is an additive entity migration

Adding durable Study-domain entities requires `SCHEMA_VERSION = 4`.

The expected v3 -> v4 migration follows the established additive migration pattern:

- existing schema-v3 projects contain none of the new Study entity types;
- migration advances the persisted Project/schema marker;
- existing v3 scientific entity payloads remain byte-for-byte unchanged;
- codec/ProjectBundle support is extended for the new entities;
- migrated projects must round-trip with the same pre-existing scientific hashes and provenance.

Any implementation that requires rewriting existing v3 scientific content must stop for a new
architecture decision.

### 10. Desktop integration gets a separate typed Study contract

The v1.0 workspace/frontend handoff remains stable.

Study-specific desktop reads/actions should use a separate versioned Study contract rather than
silently adding strict fields to the frozen v1.0 handoff. The desktop remains presentation/application
only:

- no SQLite access;
- no Study/ranking scientific hash calculation in TypeScript/Rust;
- no frontend-owned ranking fallback;
- no generic mutation engine;
- no persisted UI selection masquerading as `StudySelection`.

Typed Study operations delegate to Python Study/application authorities and return explicit receipts.

## v1.1 roadmap

### Block 1 — Study domain contracts and schema-v4 migration

Objective:
Introduce immutable Study domain/value contracts, IDs, scientific-hash rules, codec/ProjectBundle
support, and the v3 -> v4 additive migration.

Acceptance:
Legacy schema-v3 stores migrate without rewriting existing entity payloads; new Study entities
round-trip; package becomes `1.1.0.dev0`; schema becomes 4; full v1.0 CI remains green.

### Block 2 — Exact membership, provenance, and Study freshness

Objective:
Freeze canonical membership/design identity, source-hash recording, Study provenance dependencies,
and fail-closed stale/block behavior.

Acceptance:
UI/order changes do not alter scientific identity; membership/design changes do; same-UUID scientific
source drift propagates through `DependencyKind.SCIENTIFIC`.

### Block 3 — Typed sweep planner and scientific-assignment resolution

Objective:
Expand explicit supported Study axes into exact member assignments through existing
Method/Protocol/Recipe/Structure authorities.

Acceptance:
No arbitrary INCAR mutation path; scientific changes produce distinct existing identities;
execution-only settings never change Study/Calculation scientific identity.

### Block 4 — Study materialization and workflow reconciliation

Objective:
Create/reuse per-member ScientificWorkflowPlans/Calculations while keeping Study and WorkflowPlan
semantics separate.

Acceptance:
Multi-root cohort materialization, idempotent reuse, exact candidate-to-workflow bindings, historical
generation preservation, foreign/stale source rejection.

### Block 5 — Cohort execution/readiness orchestration

Objective:
Provide high-throughput readiness/status/submission projections and typed batch application actions
over existing ExecutionAttempt/RemoteJob authorities.

Acceptance:
Scheduler success remains separate from scientific convergence; batch concurrency/execution tuning is
non-scientific; partial failures do not corrupt other Study members.

### Block 6 — Scientific metric binding, eligibility, and comparability

Objective:
Bind type-specific scalar scientific metrics from existing trusted analyses/results to Study members
and establish explicit comparability/eligibility contracts.

Acceptance:
No arbitrary JSON-pointer metric extraction; units/conditions preserved; incompatible scientific
methods or conditions fail closed; stale/missing/unconverged policy is explicit.

### Block 7 — Durable ranking and screening selection

Objective:
Materialize `StudyRanking` and `StudySelection` with deterministic scientific policies, provenance,
and freshness.

Acceptance:
Criterion/direction/filter/missing-data/comparability/normalization/tie policies participate in
identity; all contributing sources are scientific dependencies; ties are not broken by display state;
source drift stales ranking/selection.

### Block 8 — Study workspace, reporting, desktop actions, and export

Objective:
Expose Study inventory, sweep matrix, member readiness, metric comparison, ranking/selection,
provenance, and deterministic exports through separate typed Python/desktop contracts.

Acceptance:
Desktop does not recompute scientific truth; A/B project switching/restart remains stateless;
scientific selection requires a typed Python action; report/export carries exact ids, hashes, units,
conditions, policies, stale/blocked reasons, and contract versions.

### Block 9 — Final schema-v4 Study E2E acceptance and hardening

Objective:
Prove migration, Study creation, sweep resolution, multi-member materialization, partial execution
states, metric binding, ranking, selection, scientific-source drift, desktop presentation, and Windows
packaged sidecar behavior as one end-to-end contract.

Acceptance:
Exact-head and exact-main CI pass Python 3.11/3.12/3.13, Ruff, strict mypy, pytest, MatterViz, Linux
desktop, Windows frozen-sidecar Study E2E, Rust/Tauri guards, NSIS output verification/artifact, plus
anchored self-review and unchanged-head merge guard.

## Explicitly deferred beyond v1.1

- NEB / transition-state / barrier workflows;
- solvation-model production support;
- charged-cell and electric-field production identity extensions beyond existing dormant contracts;
- constant-potential / grand-canonical DFT;
- microkinetics and kinetic fitting;
- ML/surrogate-driven scientific screening;
- full Pourbaix construction;
- multi-user/server collaboration;
- Linux/macOS production packaging parity if it compromises the Study/schema-v4 acceptance axis.

## Consequences

v1.1 becomes the durable high-throughput scientific-design layer that v1.0 intentionally deferred.
The key architectural cost is a deliberate schema-v4 migration and new provenance-bearing Study
entities. The benefit is that sweep, ranking, and screening become reproducible scientific objects
instead of UI conventions, while all VASP calculation, workflow, freshness, execution, and desktop
contracts remain authoritative in their existing layers.
