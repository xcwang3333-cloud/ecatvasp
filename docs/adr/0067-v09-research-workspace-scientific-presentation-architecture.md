# ADR-067: v0.9 Research Workspace and Scientific Presentation Architecture

- Status: Accepted
- Date: 2026-09-06

## Context

ECatVASP v0.1 through v0.8 now provide a durable scientific core spanning electrocatalyst structure
construction, VASP input preparation, local/remote execution, scientific-result intake and
convergence, workflow orchestration, electronic-structure analyses, thermochemistry, CHE, generic
reaction free energies, electrocatalytic descriptors, and reaction-diagram datasets.

The remaining gap to a usable research workbench is no longer primarily another scientific
calculator. The repository has only a skeletal Python `api` package and a presentation-boundary
MatterViz adapter. There is no project workspace read model, no coherent project inventory for a UI
or CLI, no user-facing scientific/provenance inspection surface, and no deterministic reporting or
export layer.

At the same time, several attractive directions -- high-throughput studies, barriers/NEB,
solvation, constant-potential/grand-canonical DFT, microkinetics, and ML -- introduce new scientific
identity, provenance, workflow, or persistence questions. Pulling them into v0.9 would expand the
scientific model before the existing core is usable as a workbench.

## Decision

v0.9 is **Research Workspace and Scientific Presentation**, not a new physical-method phase.

The phase builds a read/application layer over the existing source-of-truth entities and derived
scientific datasets. It must make the mature core inspectable and operable without introducing a
second scientific state machine or allowing presentation state to become scientific authority.

### 1. Project Workspace is the primary v0.9 boundary

A workspace is a deterministic projection over a validated `ProjectBundle`. It may summarize and
index catalysts, structures, active sites, adsorption states, calculations, execution attempts,
remote jobs, artifacts, analyses, workflow plans, provenance, dependencies, freshness, and
reconciliation results.

Workspace projections are read models only. They are not added to `ProjectBundle`, are not
provenance subjects, and do not change `SCHEMA_VERSION`.

### 2. Scientific status, execution status, and scheduler status stay separate

Any workspace/dashboard surface must preserve the existing distinctions:

- `CalculationScientificStatus` is a scientific lifecycle;
- `ExecutionAttemptStatus` is an execution-attempt lifecycle;
- `SchedulerState` is a scheduler observation;
- `AnalysisStatus` is a derived-analysis lifecycle.

No aggregate UI state may collapse scheduler success into scientific convergence.

### 3. Presentation consumes scientific DTOs; it does not own them

MatterViz remains an adapter target for structure/trajectory/volumetric views. DOS/PDOS,
charge-density, COHP/ICOHP, thermochemistry, reaction pathway, and reaction-diagram presentation
must consume canonical ECatVASP datasets or explicit presentation DTOs derived from them.

Figure styling, axis ranges, labels, colors, camera state, panel layout, and export format are
presentation concerns and never become scientific provenance sources.

### 4. Reporting is deterministic export, not a second analysis engine

v0.9 may generate JSON/CSV/Markdown-oriented scientific reports and manifests from existing durable
entities and datasets. A report must retain source identifiers, scientific status/freshness, units,
definitions, conditions, and provenance links where relevant.

A report must not recompute alternative scientific values, infer species from filenames, or hide
stale/blocked inputs.

### 5. Experiment-like UX is an application service, not persisted workflow history

User-facing operations such as "prepare", "run", "inspect", "promote", "analyze", and "report"
may be exposed through application services/CLI contracts. Those services orchestrate existing
scientific and execution primitives and must respect existing gates.

No v0.9 UX/session/task-board state becomes a competing persisted scientific workflow state machine.
Historical `ScientificWorkflowPlan` generations remain authoritative for calculation workflows.

### 6. Desktop implementation remains downstream of stable contracts

ADR-008's Tauri + Svelte/TypeScript direction remains a preferred desktop candidate, but v0.9 does
not make a desktop framework a scientific dependency. v0.9 first stabilizes Python workspace,
presentation, reporting, and application-service contracts that a desktop client can consume.

A desktop packaging/benchmark spike may occur late in v0.9, but a production desktop shell is a v1.0
concern unless it can be added without changing the scientific core or introducing a major runtime
boundary.

### 7. Schema and scientific identity remain frozen unless a later block proves otherwise

The default v0.9 target is `SCHEMA_VERSION = 3`. Workspace projections, presentation DTOs, reports,
and command results are transient or externally exported views.

If a proposed feature needs a new permanent top-level entity, scientific identity dimension, or
migration, that feature must stop for explicit architecture review rather than being smuggled into a
presentation block.

## Candidate directions considered

### A. Workspace + presentation + reporting -- selected

Benefits:

- directly closes the usability gap exposed by the current repository;
- naturally consumes the mature v0.1-v0.8 domain model;
- keeps `SCHEMA_VERSION = 3` and reuses existing provenance/freshness machinery;
- creates stable contracts for CLI, desktop, notebooks, and future automation;
- makes existing scientific capabilities discoverable before adding more physics.

Primary risk: accidentally creating a second state model in the name of UX. This is controlled by
making workspace and reporting outputs pure projections.

### B. Visualization-first desktop phase -- not selected as the primary plan

Benefits: immediate visible product progress.

Costs: the existing MatterViz adapter already proves the rendering boundary, while a full GUI would
need application/read-model contracts that do not yet exist. Starting with screens would encourage
frontend-owned state and ad-hoc data access. Visualization remains an important v0.9 block, but it is
built on workspace contracts rather than leading the architecture.

### C. High-throughput/scientific-method expansion -- deferred

Benefits: adds new research capability.

Costs: parameter sweeps/studies likely require durable study identity, aggregation provenance,
selection semantics, and possibly schema evolution. NEB, solvation, constant-potential DFT,
microkinetics, and ML each introduce additional scientific correctness and provenance boundaries.
Those expansions should not be mixed with the productization phase.

## Planned Blocks

1. **Workspace projection contracts** -- deterministic validated `ProjectBundle` read model,
   explicit entity/status summaries, and non-scientific projection identity.
2. **Scientific inventory and provenance inspection** -- source-linked rows for structures,
   calculations, artifacts, analyses, dependencies, freshness, and stale/blocked reasons.
3. **Workflow/execution readiness dashboard** -- projection of workflow generations, scientific
   gates, attempts/jobs, reconciliation, and actionable blockers without mutating history.
4. **Scientific visualization presentation datasets** -- stable presentation DTOs/adapters for
   structures plus electronic-analysis and reaction-diagram datasets; MatterViz remains a consumer.
5. **Scientific reporting and export** -- deterministic JSON/CSV/Markdown-oriented manifests and
   report sections with provenance, conditions, definitions, units, and freshness visibility.
6. **Experiment-like application services** -- safe high-level prepare/run/inspect/promote/analyze/
   report operations composed from existing primitives and gates.
7. **Headless CLI and Python application facade** -- usable project-open/inspect/status/report
   workflows without making CLI state authoritative.
8. **Frontend handoff and workspace interoperability** -- stable serialized presentation contracts,
   MatterViz/desktop handoff tests, and a Tauri/Svelte implementation benchmark without changing the
   scientific source of truth.
9. **Final E2E acceptance and hardening** -- reopen a real project, inspect workspace state, trace
   provenance/freshness, render/export canonical presentation datasets, exercise application/CLI
   paths, and prove all stale/blocked and execution-vs-science boundaries remain fail-closed.

ADR-068 through ADR-076 are reserved for those Blocks in order.

## Final v0.9 E2E acceptance contract

v0.9 is complete only when a project persisted by `ProjectStore` can be reopened and consumed through
one deterministic workspace/application surface that can:

1. enumerate its major scientific, workflow, execution, artifact, and analysis objects without
   filename inference;
2. show scientific, analysis, execution-attempt, and scheduler states without collapsing them;
3. expose exact provenance/dependency/freshness links and actionable stale/blocked reasons;
4. produce presentation-ready structure/electronic/reaction datasets from canonical scientific
   sources without presentation becoming scientific authority;
5. export a deterministic scientific report/manifest retaining identifiers, units, conditions,
   definitions, and freshness/provenance context;
6. exercise representative prepare/inspect/analyze/report operations through the same application
   contracts used by CLI/frontend clients;
7. preserve `SCHEMA_VERSION = 3`, existing scientific identities, historical workflow generations,
   and all v0.1-v0.8 acceptance contracts unless an explicitly approved later ADR changes them; and
8. fail closed after source, policy, method, structure, or dependency drift instead of presenting
   stale outputs as current.

## Deferred beyond v0.9

The following are deliberately outside the selected v0.9 architecture unless separately approved:

- durable high-throughput `Study`/sweep entities and cross-candidate ranking;
- barriers, transition states, or NEB;
- implicit/explicit solvation and electric-field corrections;
- charged-cell or constant-potential/grand-canonical DFT;
- microkinetics and kinetic parameter fitting;
- ML training/inference pipelines;
- full Pourbaix engines;
- production desktop packaging/distribution;
- database-server or multi-user collaboration infrastructure.

High-throughput studies and a production desktop workspace are natural v1.0 candidates after the
v0.9 application contracts are stable. The other deferred scientific methods should enter later
method-focused phases with their own identity/provenance reviews.