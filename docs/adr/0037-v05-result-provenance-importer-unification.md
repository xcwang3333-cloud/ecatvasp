# ADR-037: v0.5 Result Provenance, Freshness, and Existing-Import Unification

- Status: Accepted
- Date: 2026-09-04

## Context

Blocks 1-7 established parser-neutral VASP result contracts, verified result intake,
energy/metadata parsing, recipe-aware convergence classification, final force/magnetization
observables, deterministic CONTCAR reconstruction/promotion, and finite-difference frequency
results. Those layers intentionally remained pure: they did not persist the complete scientific
result chain or mutate lifecycle state as a side effect.

The frozen project model already persists `Analysis`, `Artifact`, `ProvenanceRecord`, and
`DependencyRecord`, and the existing `FreshnessEngine` already propagates `STALE` and `INVALID`
through `SCIENTIFIC` dependencies. Replacing that engine would duplicate correct architecture.

The remaining inconsistency is the original v0.1 existing-folder importer. It writes a legacy
`PARSED_RESULT` directly from an `ExecutionAttempt`, derives convergence from ad hoc booleans,
sets `Calculation.status` inside the importer parser, and promotes a relax CONTCAR regardless of the
new explicit convergence boundary. Historical folders also have no ECatVASP `ExecutionPlan` or
`atom-index-map.json`, so unification must not fabricate those identities.

## Decision

### 1. Persist the normalized scientific result as an Analysis/Artifact DAG

The durable v0.5 result chain is:

`Calculation + raw result Artifacts -> RESULT_PARSE Analysis -> PARSED_RESULT Artifact -> CONVERGENCE Analysis -> convergence Artifact`.

Both derived Artifacts are produced by `AnalysisProducerRef`, never by the
`ExecutionAttempt` that produced the raw VASP files.

`RESULT_PARSE` records all exact admitted raw result Artifacts as scientific inputs. The
`CONVERGENCE` Analysis records the normalized parsed-result Artifact plus the exact OUTCAR and,
when present, OSZICAR evidence that the convergence classifier rereads.

The convergence Artifact uses `ArtifactType.DERIVED_DATASET` with format
`ecatvasp-vasp-convergence-assessment`, version 1. The normalized parsed-result Artifact keeps the
Block 7 `ecatvasp-vasp-scientific-result`, version 3 contract.

### 2. Calculation status reconciliation is explicit and one-way

A pure reconciliation function maps only the overall scientific convergence verdict:

- `CONVERGED` -> `CalculationScientificStatus.CONVERGED`;
- `UNCONVERGED` -> `CalculationScientificStatus.COMPLETED_UNCONVERGED`;
- `INDETERMINATE` -> `CalculationScientificStatus.BLOCKED`.

`NOT_APPLICABLE` is invalid as an overall Calculation verdict and fails closed.

There is deliberately no dependency edge from the convergence Artifact back to the Calculation.
Such an edge would create a provenance cycle because the Calculation is already an upstream
scientific context for result parsing and convergence. Lifecycle reconciliation is an explicit
state update, not a claim that an immutable result Artifact scientifically produced the Calculation
intent.

### 3. Reuse the frozen FreshnessEngine

Block 8 does not alter freshness precedence or propagation rules. Instead it supplies the missing
scientific dependency graph. A raw result Artifact hash change therefore makes the RESULT_PARSE
Analysis stale; staleness propagates through the parsed-result Artifact, convergence Analysis, and
convergence Artifact under the existing engine.

Non-scientific dependency kinds remain non-propagating, `INVALID` remains stronger than `STALE`, and
`SUPERSEDED` remains scientifically valid and non-propagating as frozen in the Scientific Core.

### 4. Historical folder import gets an explicit compatibility intake

An existing VASP folder has no historical ECatVASP ExecutionPlan, input-manifest hash, or staged
atom-index map. The importer therefore creates an exact compatibility intake from the imported raw
Artifact identities, roles, sizes, and SHA-256 digests. It does **not** synthesize fake plan hashes
or claim that v0.3 materialization occurred in the past.

This compatibility intake is used only by the parser/convergence subset whose actual requirements
are exact content-addressed result files. Managed observables, frequency parsing, and managed
CONTCAR reconstruction continue to require their stronger ExecutionPlan/atom-index-map contracts.

Existing-folder imports remain limited to relax/static calculations in v0.5.

### 5. Existing-folder atom identity remains strict VASP-order compatibility logic

For historical folders the importer creates permanent atom UIDs from the imported POSCAR and may
propagate them to CONTCAR only by exact VASP/POSCAR order, atom count, and element identity. Any
count or species/order mismatch fails closed. No geometric nearest-neighbour matching is allowed.

This is intentionally distinct from managed Block 6 reconstruction, which requires the immutable
v0.3 `atom-index-map.json`. Historical imports cannot retroactively satisfy that stronger provenance
contract.

### 6. CONTCAR reconstruction and promotion are separated for imports too

A relax CONTCAR is retained as an immutable final candidate even when the Calculation is
unconverged. It becomes `StructureVariant.current_structure_snapshot_id` only when the normalized
convergence assessment is `CONVERGED` and the importer is not overwriting an unrelated pre-existing
current snapshot.

When a new variant has no current snapshot, the imported POSCAR becomes its baseline current
snapshot. An unconverged relax therefore leaves the imported POSCAR current while retaining the
CONTCAR candidate for inspection.

### 7. `ParsedVaspResult` becomes a compatibility projection

The public compatibility object remains available so existing callers do not lose the v0.1 surface.
Its fields are derived from normalized v0.5 contracts:

- legacy `total_energy_ev` explicitly projects `VaspEnergySummary.free_energy_toten_ev`;
- convergence booleans project `CONVERGED -> True`, `UNCONVERGED -> False`, and
  `INDETERMINATE/NOT_APPLICABLE -> None`;
- `scientific_status` comes only from the centralized reconciliation mapping;
- the legacy max-force scalar remains a compatibility summary for historical folders because those
  folders lack the managed atom-index-map required by the Block 5 UID-bound force dataset.

The durable PARSED_RESULT file itself uses the normalized v0.5 result document and does not restore
legacy ambiguous fields.

### 8. Imported source files are not silently moved

Block 8 keeps imported raw Artifact metadata bound to the exact external source files supplied by
the caller. Scientific source roles used by parsing/convergence are content-addressed and rechecked
against their recorded sizes/hashes. Copying, archiving, or ingesting external folders into project
managed storage is a separate product policy and is not introduced here.

### 9. No storage-schema migration

Project `SCHEMA_VERSION` remains 2. All durable records use existing Analysis, Artifact,
ProvenanceRecord, DependencyRecord, Calculation, and StructureSnapshot entities. Result JSON format
versions are artifact-level contracts, not Project schema versions.

## External implementation references

The boundary was cross-checked against mature open-source VASP workflow projects without adding a
runtime dependency:

- atomate2 creates structured task documents from calculation directories while keeping those
  result documents distinct from job/workflow execution;
- aiida-vasp exposes parser outputs/structures while treating execution and convergence failures as
  explicit, separate semantics.

ECatVASP keeps its smaller immutable Analysis/Artifact/provenance model and does not import either
project's database or workflow abstractions.

## Non-scope

Block 8 does not add:

- new VASP scientific quantities;
- DOS/PDOS, Bader, charge difference, COHP, band-center analysis;
- ZPE, entropy, thermal corrections, CHE, or free-energy calculations;
- automatic restart, correction, continuation, or multi-step workflow orchestration;
- managed frequency/observable parsing for historical folders without required provenance;
- fabricated historical ExecutionPlans or atom-index maps;
- nearest-neighbour atom identity reconstruction;
- source-folder copying/archival policy;
- GUI changes;
- Project schema migration;
- tag, GitHub Release, or PyPI publication.

## Consequences

Managed results and existing-folder imports now converge on the same normalized result and
convergence semantics, and downstream freshness can follow one explicit scientific DAG. Block 9 can
therefore perform end-to-end v0.5 acceptance without carrying the original v0.1 direct-result and
auto-promotion exceptions into later scientific workflow or thermochemistry phases.
