# ADR-065: v0.8 Durable Reaction Diagram and Reconciliation Boundary

- Status: Accepted
- Date: 2026-09-06

## Context

ADR-057 reserves Block 8 for durable reaction-diagram data plus thermochemistry/reaction
reconciliation. Blocks 1-7 already provide parameter-complete thermochemistry, gas references,
explicit correction policies, CHE conditions, generic signed reaction stoichiometry, and
potential-dependent descriptors. The remaining problem is to make those results durable and
workflow-aware without creating another persisted workflow state machine or weakening the frozen
v0.5-v0.7 provenance boundaries.

## Decision

### 1. Reaction diagrams are scientific datasets, not figures

A requested-condition free-energy diagram is materialized as:

`exact thermochemistry Artifacts -> REACTION_DIAGRAM Analysis -> DERIVED_DATASET`

The canonical dataset contains ordered state keys, requested-condition cumulative and step free
energies, the exact pathway-definition hash, baseline pathway-result hash, baseline/requested CHE
conditions, source receipts, and any explicitly attached descriptor definitions. Plotting remains a
future deterministic consumer and is not authoritative scientific state.

### 2. Materialization re-evaluates the pathway

The materializer accepts the Block 6 `ReactionPathwayDefinition` and exact source registry and calls
the generic evaluator again. It does not accept an arbitrary table of precomputed Delta-G values as
scientific truth. Potential/pH transformation is then derived through the Block 7 CHE affine view.
Reaction-family names never select equations or sign conventions.

### 3. Every non-CHE source is bound to exact durable thermochemistry

Each non-CHE reaction source must bind one completed `THERMOCHEMISTRY` Analysis and its exact local
or locally mirrored `DERIVED_DATASET` Artifact. Before materialization, Block 8 verifies:

- Analysis type, tool, and tool version;
- canonical thermochemistry format and version;
- Artifact producer identity, byte size, and SHA-256;
- `source_receipt_hash == Analysis.parameters_hash`;
- canonical source-receipt self-hash;
- durable result hash and full result payload against the in-memory scientific source.

CHE remains a parameter-complete value source rather than a fabricated file Artifact. Its condition
identity and hydrogen-reference identity are already part of the pathway/view hashes.

### 4. Descriptor definitions are content-addressed

Limiting potential, reversible potential, OER theoretical overpotential, and HER Delta-G(H*) may be
attached as typed descriptor definitions. Their identity includes descriptor kind, value, unit,
source-result hash, and, where applicable, exact pathway and baseline-result hashes. A descriptor is
not persisted as an unqualified scalar label.

### 5. Scientific freshness uses the existing DAG

For each durable non-CHE source, both its source Analysis and source Artifact are recorded as
`DependencyKind.SCIENTIFIC` upstreams of the REACTION_DIAGRAM Analysis. The diagram Analysis is then
a SCIENTIFIC upstream of its output Artifact. Any source-analysis identity drift or Artifact-content
drift therefore propagates through the existing `FreshnessEngine`; no reaction-specific freshness
engine is introduced.

### 6. Thermochemistry/reaction reconciliation is a pure projection

Block 8 adds an ephemeral requirement/projection layer for `THERMOCHEMISTRY` and
`REACTION_DIAGRAM` Analysis identities. Exact identity matching uses:

- project id;
- Analysis type;
- exact ordered input Artifact ids;
- parameter hash.

The reconciler derives UNMATERIALIZED, IN_PROGRESS, COMPLETED, BLOCKED, FAILED, STALE, INVALID, or
SUPERSEDED from immutable ProjectStore facts, Analysis lifecycle state, Artifact availability,
SCIENTIFIC freshness, provenance edges, and optional workflow-gate evidence. It persists none of
those projection states.

### 7. Reaction requirements may have multiple workflow anchors

A thermochemistry requirement commonly depends on one current frequency Calculation generation. A
reaction diagram can depend on several slab, adsorbate, or gas preparation steps. Therefore an
ephemeral requirement may carry multiple unique `(step_key, calculation_id)` anchors.

Every anchor must resolve exactly once against the supplied v0.6 `WorkflowScientificGateEvaluation`.
If an anchored step advances to another Calculation generation, the historical requirement becomes
`SUPERSEDED / BLOCKED`; it is never silently re-anchored. INVALID, STALE, SUPERSEDED, BLOCKED, and
WAITING workflow evidence is projected fail-closed using the existing v0.6 semantics.

### 8. Reopen is deterministic

`reconcile_thermochemistry_analyses_from_store()` reopens the existing `ProjectStore`, reconstructs
current scientific hashes for all supported provenance entities, and recomputes the same projection.
Explicit current-hash overrides are accepted only as test/inspection inputs and remain subject to
normal SHA-256 validation.

### 9. Schema and dependency boundaries remain frozen

`AnalysisType.REACTION_DIAGRAM` extends the existing generic persisted enum vocabulary. ProjectStore
already persists `Analysis` and `Artifact` through generic entity/enum codecs, so Block 8 requires no
new table, top-level ProjectBundle entity, or schema migration. `SCHEMA_VERSION` remains 3. No new
runtime dependency is added.

## Fail-closed boundaries

Block 8 fails rather than guessing when source species are missing or extra, non-CHE sources lack an
exact durable binding, source bytes or receipt identity differ, a completed Analysis lacks required
SCIENTIFIC input/output edges, a workflow anchor cannot resolve exactly, a historical Calculation is
no longer current, required Artifact hashes are absent, or a persisted scientific hash cannot be
resolved.

## Consequences

The UI can later render free-energy diagrams from one canonical dataset without becoming a source of
scientific truth. Thermochemistry and electrocatalytic readiness remain projections over the same
Analysis/Artifact/ProjectStore/FreshnessEngine architecture used elsewhere in ECatVASP, while
multi-Calculation reaction provenance remains explicit and historical generations cannot silently
be reused.

## Deferred

Block 9 owns final v0.8 end-to-end acceptance, representative HER/ORR/OER/CO2-to-CO reopen tests,
additional fail-closed hardening discovered at E2E scale, and final phase completion. Plotting/GUI,
constant-potential DFT, barriers, microkinetics, explicit-solvent MD, and full Pourbaix construction
remain outside v0.8.
