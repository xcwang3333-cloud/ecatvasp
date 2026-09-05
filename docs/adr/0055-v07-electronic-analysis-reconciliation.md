# ADR-055: v0.7 Electronic Analysis Reconciliation and Workflow Integration Boundary

## Status

Accepted for v0.7 Block 8.

## Context

v0.7 Blocks 1-7 established durable electronic-analysis identities for canonical DOS/PDOS, Bader,
charge-density difference, LOBSTER COHP/ICOHP, and band/p-band/d-band descriptors. Those analyses
already use the existing `Analysis`, `Artifact`, `AnalysisProducerRef`, `ProvenanceRecord`,
`DependencyRecord`, and `FreshnessEngine` contracts.

v0.6 already owns the persisted scientific workflow model. `ScientificWorkflowPlan` remains a
Calculation-only DAG with append-only `WorkflowStepBinding` generations, explicit scientific gates,
and a pure reconciliation/acceptance layer. Electronic analyses therefore need an integration view
without becoming new workflow steps and without introducing another persisted state machine.

## Decision

### 1. Reconciliation is a pure workflow-side projection

Block 8 adds a workflow-side electronic-analysis reconciliation layer. It consumes persisted scientific
facts and returns immutable value objects describing current analysis scientific state and readiness.

The projection is not persisted in `ProjectBundle`, is not a provenance subject, and does not mutate
`Analysis.status`, `ScientificWorkflowPlan`, `WorkflowStepBinding`, Calculation identity, or workflow
history. Recomputing the projection from unchanged project state must return the same report hash.

The dependency direction remains one-way: workflow integration may consume analysis contracts;
analysis modules do not import the workflow package.

### 2. Desired analysis identity is explicit and ephemeral

An `ElectronicAnalysisRequirement` contains:

- a caller-stable requirement key;
- exact `ProjectId`;
- exact electronic `AnalysisType`;
- ordered exact `input_artifact_ids`;
- exact `parameters_hash`;
- optionally an exact v0.6 workflow anchor.

For reconciliation, an existing Analysis matches only when project, analysis type, ordered input
Artifact ids, and parameters hash all match exactly. Tool and tool version remain part of the Analysis
scientific hash and durable provenance, but the requirement does not guess a tool implementation.

Zero exact matches means the requested analysis is not yet materialized. More than one exact match is
an integrity ambiguity and fails closed. Reconciliation never chooses the newest Analysis, newest file,
or newest workflow generation heuristically.

### 3. Existing freshness semantics remain authoritative

Block 8 reuses `FreshnessEngine` and `DependencyKind.SCIENTIFIC` without modification. It does not add
an electronic-specific freshness engine.

`FRESH`, `STALE`, `INVALID`, and `SUPERSEDED` are projected into electronic-analysis state. Scientific
hash drift in an upstream Artifact therefore propagates through the already persisted dependency DAG
to its Analysis and analysis-produced Artifacts.

ExecutionAttempt and scheduler lifecycle remain outside electronic scientific freshness unless an
existing dependency explicitly and validly models a non-scientific relationship.

### 4. Input availability and scientific validity are separate

For an exact requirement with no existing Analysis:

- fresh, hashed, locally available (`LOCAL` or `BOTH`) inputs permit `READY`;
- fresh, hashed `REMOTE` or `ARCHIVED` inputs produce `WAITING` because retrieval is required;
- `MISSING` input produces `BLOCKED`;
- stale, invalid, or superseded input propagates that scientific state and blocks materialization;
- an input Artifact without SHA-256 is invalid scientific evidence.

Availability does not change scientific hashes and does not manufacture a new analysis identity.

### 5. A completed Analysis requires a complete scientific chain

A persisted `AnalysisStatus.COMPLETED` is not sufficient by itself for reconciliation satisfaction.
The exact requirement must have:

1. at least one exact matching Analysis;
2. a SCIENTIFIC dependency from every exact required input Artifact to that Analysis;
3. at least one Artifact produced by `AnalysisProducerRef` for that Analysis;
4. a SCIENTIFIC dependency from the Analysis to every such output Artifact;
5. fresh scientific state for the Analysis and outputs;
6. SHA-256 identity for every output Artifact.

Missing scientific edges are classified as invalid reconciliation evidence rather than being inferred
from filenames, timestamps, producer order, or directory layout.

Fresh local/both outputs allow `SATISFIED`. Fresh remote/archived outputs keep the scientific state
`COMPLETED` but readiness `WAITING` until the result is locally available for the next consumer.
Missing completed outputs are invalid.

### 6. Analysis lifecycle status is projected, not rewritten

For an exact fresh Analysis:

- `DRAFT`, `READY`, and `RUNNING` project as `IN_PROGRESS / WAITING`;
- `BLOCKED` projects as `BLOCKED`;
- `FAILED` projects as `FAILED`;
- persisted `STALE` and `INVALID` remain blocking states;
- `COMPLETED` proceeds to provenance/output validation.

The reconciler never writes any replacement lifecycle status. Freshness remains derived from the
scientific dependency graph rather than being copied into mutable rows.

### 7. Workflow integration anchors to one exact current generation

An optional `ElectronicWorkflowAnchor` contains only a workflow `step_key` and the exact expected
`CalculationId`. The supplied v0.6 `WorkflowScientificGateEvaluation` must resolve that step exactly
once and its current binding/current Calculation must agree with the anchor.

If the workflow has advanced to a newer binding generation, the old anchored requirement becomes
`SUPERSEDED / BLOCKED`. The reconciler never silently reanchors an analysis to the newest Calculation.

A current workflow step that is not yet scientifically satisfied contributes `WAITING`; it does not
rewrite an already materialized electronic Analysis as unmaterialized. Invalid/stale/superseded or
blocked workflow gate evidence blocks the anchored analysis projection.

This is an integration reference only. Electronic analyses are not added to `ScientificWorkflowPlan`
and do not create `WorkflowStepBinding` rows.

### 8. Durable reopen recomputes, rather than persists, reconciliation

`reconcile_electronic_analyses_from_store()` reopens the `ProjectStore`, rebuilds current scientific
hashes from the persisted scientific entity families supported by `scientific_hash`, and evaluates the
same pure reconciliation function.

An unchanged saved/reopened bundle must reproduce the same projections and report hash. Tests may
supply explicit current-hash overrides to model upstream drift; overrides are normalized as SHA-256
values and remain transient test/runtime evidence.

This gives Block 8 reopen/idempotency hardening without a reconciliation table, migration, or second
workflow state machine.

### 9. Scope and schema remain frozen

`SCHEMA_VERSION` remains 3. No runtime dependency is added.

Block 8 does not implement:

- new electronic Analysis entities beyond the existing enum/contracts;
- automatic execution of Bader or LOBSTER on a scheduler;
- mutation of workflow plans or binding generations;
- automatic retry/recovery policy for external analyses;
- thermochemistry, ZPE, entropy, gas reference energies, CHE, free-energy diagrams, potential/pH
  corrections, or other v0.8 scope;
- GUI or a new database/workflow framework.

## Consequences

Electronic analyses can now be queried as current, waiting, blocked, stale, superseded, or satisfied
against the same immutable provenance and v0.6 workflow generation semantics used elsewhere in
ECatVASP. Reconciliation remains deterministic, fail-closed, and disposable: persisted scientific
facts are the authority, while the reconciliation report is only a current projection of those facts.
