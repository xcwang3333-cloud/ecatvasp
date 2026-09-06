# ADR-068: v0.9 Block 1 Workspace Projection Contracts

- Status: Accepted
- Date: 2026-09-06

## Context

ADR-067 selects Research Workspace and Scientific Presentation as the v0.9 direction. The first
implementation boundary must expose one deterministic project-level read model without creating a
new persisted workspace/session state machine.

The existing `ProjectBundle` already contains the authoritative project graph. It also deliberately
separates scientific calculation status, execution-attempt status, scheduler state, and analysis
status. A usable application layer needs a stable summary of that graph while preserving those
boundaries exactly.

## Decision

### 1. Workspace projection is a pure read model

`build_workspace_projection()` consumes one validated `ProjectBundle` and returns an immutable
`WorkspaceProjection`.

The projection contains project metadata, deterministic counts for every persisted entity family,
and separate observed-status summaries for calculations, analyses, execution attempts, and remote
scheduler jobs.

### 2. Project integrity is checked before presentation

Projection construction calls `ProjectBundle.validate()` and fails closed on graph corruption. A UI,
CLI, report, or API must not receive a friendly-looking summary of an invalid project graph.

### 3. Status domains are never collapsed

The projection does not invent a generic `SUCCESS`, `DONE`, or `RUNNING` state across layers.
`CalculationScientificStatus`, `AnalysisStatus`, `ExecutionAttemptStatus`, and `SchedulerState` remain
separate status dimensions.

In particular, scheduler completion is not mapped to scientific convergence.

### 4. Projection identity is non-scientific

`WorkspaceProjection.projection_hash` is a deterministic hash of the projection payload. It exists
for presentation caching/equality and reproducible reporting only.

It is **not** an `Analysis.parameters_hash`, scientific identity, provenance record, freshness input,
or replacement for persisted entity identifiers.

### 5. No persistence or schema expansion

No `Workspace`, `Dashboard`, `Session`, or `ProjectView` entity is added to `ProjectBundle`.
`SCHEMA_VERSION` remains 3. No migration is introduced.

### 6. No runtime dependency expansion

The implementation uses only the Python standard library plus existing ECatVASP domain/storage
contracts. No plotting, database, web, or desktop dependency is added.

## Acceptance criteria

Block 1 is accepted when:

- a valid project produces a deterministic immutable workspace projection;
- all persisted entity families are counted explicitly;
- calculation, analysis, execution-attempt, and scheduler statuses are summarized separately;
- identical projection payloads produce identical `projection_hash` values;
- project metadata or visible entity/status changes alter the projection hash;
- an invalid `ProjectBundle` is rejected before projection;
- projection construction creates no new persisted entity, provenance record, dependency record, or
  scientific hash;
- package version enters `0.9.0.dev0` while `SCHEMA_VERSION = 3`; and
- Ruff, mypy strict, pytest on Python 3.11/3.12/3.13, and MatterViz contract remain green.

## Consequences

Later v0.9 blocks can add inventory rows, provenance/freshness inspection, dashboards, reporting,
CLI, and frontend DTOs above this projection boundary without teaching presentation code to traverse
raw `ProjectBundle` internals ad hoc.