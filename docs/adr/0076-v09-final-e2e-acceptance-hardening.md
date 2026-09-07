# ADR-076: v0.9 Final E2E Acceptance and Hardening

- Status: Accepted
- Date: 2026-09-07

## Context

v0.9 Blocks 1 through 8 progressively added workspace projection, provenance/freshness inspection,
workflow/execution readiness, scientific presentation DTOs, deterministic reporting, application
services, headless Python/CLI access, and a versioned frontend handoff envelope.

The final Block must prove these layers behave as one workbench over the same durable ProjectStore,
not merely as isolated unit-tested components. In particular, a presentation or report produced
before a scientific source changes must never remain acceptable merely because its entity UUID is
unchanged.

## Decision

### 1. Final acceptance uses one persisted project across layers

The Block 9 acceptance fixture persists one validated project containing:

- a `StructureSnapshot`;
- a completed `Analysis`;
- an explicit `ProvenanceRecord` for that analysis;
- a `DependencyKind.SCIENTIFIC` edge from the structure to the analysis;
- a `ScientificWorkflowPlan` rooted at the same structure.

The project is reopened through `ProjectStore` / `ProjectFacade`, not passed only as an in-memory
bundle.

### 2. The same reopened state must drive all v0.9 read/application surfaces

The acceptance path exercises:

- workspace projection and scientific inventory;
- authoritative freshness;
- workflow readiness dashboard;
- MatterViz-backed structure presentation;
- scientific reporting through the frontend handoff;
- path-scoped Python facade;
- headless CLI status JSON.

These are projections over one durable project. None may create an independent scientific state.

### 3. Workflow readiness remains separate from scientific result status

The fixture intentionally keeps workflow steps unmaterialized/blocked while the independent geometry
analysis is completed and fresh. This proves the final workspace can expose different lifecycle
namespaces simultaneously rather than synthesizing one project-level success value.

### 4. Persisted scientific source drift is the final fail-closed test

After the first successful handoff is built, Block 9 changes the coordinates of the existing
`StructureSnapshot` while retaining its permanent entity ID. The modified bundle is saved back to
the same ProjectStore.

The frozen scientific dependency still records the previous structure hash. On reopen:

- the downstream analysis must evaluate `STALE` with `scientific_hash_changed`;
- CLI status must expose the stale freshness and attention count;
- the old structure presentation must be rejected because its source scientific hash no longer
  matches the current `StructureSnapshot`;
- rebuilding a current structure presentation is allowed, but the stale downstream analysis must
  remain visibly stale in the new report/handoff.

UUID continuity therefore cannot hide scientific-content drift.

### 5. No final-block repair may weaken earlier contracts

If Block 9 exposes a defect, the repair must occur at the narrowest owning layer. The final tests
must not suppress provenance/freshness errors, infer currentness from IDs, or introduce a second
validator merely to make the E2E fixture pass.

### 6. v0.9 remains a productization phase, not a scientific-method expansion

Block 9 adds no Study entity, NEB/barrier model, solvation, constant-potential method,
microkinetics, ML, desktop packaging, or collaboration server.

The phase closes with:

- package version `0.9.0.dev0`;
- `SCHEMA_VERSION = 3`;
- no tag;
- no GitHub Release;
- no PyPI publication.

## Final v0.9 acceptance contract

v0.9 is accepted when exact-head and post-merge CI prove that a durable ProjectStore can be reopened
and consumed through one coherent application/workspace surface that:

1. enumerates current scientific, workflow, execution, artifact, analysis, provenance, and
   dependency state without filename inference;
2. keeps Calculation scientific, Analysis, ExecutionAttempt, Scheduler, workflow-readiness, and
   freshness namespaces separate;
3. exposes exact provenance/dependency/freshness links and actionable stale/blocked reasons;
4. produces presentation-ready structure/electronic/reaction datasets only from canonical sources;
5. exports deterministic report/frontend JSON retaining identifiers, units, conditions,
   definitions, contract versions, and freshness/provenance context;
6. exposes the same current project through Python facade and CLI rather than cached session state;
7. preserves schema v3, scientific identities, historical workflow generations, and all v0.1-v0.8
   acceptance contracts; and
8. fails closed after persisted source/policy/method/structure/dependency drift instead of presenting
   stale outputs as current.

## Acceptance evidence

`tests/test_v09_final_e2e_acceptance.py` provides the cross-layer persisted-project acceptance path.
The existing v0.1-v0.8 and v0.9 Block 1-8 test suites remain part of the same mandatory CI matrix,
so final acceptance is additive rather than a replacement for previous contracts.

## Consequences

After Block 9 is merged and its exact main post-merge CI succeeds, v0.9 Research Workspace and
Scientific Presentation is frozen. The next version should begin from that verified main and should
undergo a fresh architecture review before adding durable high-throughput Study semantics, a
production desktop workspace, or new physical methods.
