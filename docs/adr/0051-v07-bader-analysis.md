# ADR-051: v0.7 Bader Analysis Intake and Provenance Boundary

- Status: Accepted
- Date: 2026-09-05
- Scope: v0.7 Block 4

## Context

ADR-048 places Bader charge analysis outside VASP Calculation identity. The external Bader program
consumes charge-density files and emits `ACF.dat`; ECatVASP must preserve that tool boundary while
binding atom-resolved results to permanent `atom_uid` values and integrating them into the existing
Analysis/provenance/freshness model.

The frozen v0.3 `ChargeDensityStatic` recipe writes CHGCAR but explicitly uses `LAECHG=.FALSE.`. It was
designed for charge-density-difference prerequisites and must not be silently changed by v0.7.

For PAW calculations, the Henkelman Bader guidance recommends partitioning with a reference total
charge density constructed from AECCAR0 + AECCAR2 while integrating the normal valence CHGCAR. This is
a scientific input choice and therefore must be explicit in provenance rather than inferred from files
that happen to exist.

## Decision

### 1. Bader remains an external Analysis, not a VASP Calculation

Block 4 does not execute the Bader binary, add a scheduler backend, or create a new Calculation type.
It ingests exact `ACF.dat` bytes produced by an externally executed Bader command and records the exact
`ExternalToolInvocation` containing tool version, argv, and input content digests.

The VASP source remains a scientifically converged `CHARGE_STATIC` Calculation. Scheduler/process
success alone is insufficient.

### 2. Two reference modes are explicit

`BaderReferenceMode.CHGCAR_ONLY` requires exactly one external-tool input role:

- `charge_density`.

It forbids `-ref` in the recorded argv.

`BaderReferenceMode.EXPLICIT_REFERENCE` requires exactly:

- `charge_density`;
- `reference_charge_density`;
- a recorded `-ref` argument.

The reference density must also be supplied as a content-addressed ECatVASP Artifact during durable
materialization. Block 4 does not construct AECCAR0 + AECCAR2 or manufacture `CHGCAR_sum` itself.

This allows the recommended all-electron-reference workflow to be represented without modifying the
frozen v0.3 recipe or pretending an untracked reference file is reproducible.

### 3. ACF coordinates are never atom identity

`ACF.dat` atom rows are accepted only when their one-based indices are contiguous and the row count
exactly matches the frozen `atom-index-map.json`. Row `n` maps to `vasp_ordinal=n`, which supplies the
permanent `atom_uid`.

The XYZ columns in `ACF.dat` are parsed only as finite numeric facts. They are not compared to atomic
positions and are never used for nearest-neighbour, distance, species-only, or geometry-based identity
guessing.

### 4. Canonical Bader facts stop before interpretation

The normalized result stores, per permanent atom:

- integrated electron count (`CHARGE` in ACF.dat);
- minimum distance to the Bader surface;
- basin volume.

It also stores the reported total number of electrons and optional vacuum charge/volume summaries.
The result is bound to the StructureSnapshot, exact atom-map digest, explicit reference mode, and exact
external invocation provenance hash.

Block 4 does not convert integrated electron counts into oxidation states, formal charges, or a unique
chemical charge-transfer interpretation. Those require additional reference/ZVAL semantics and remain
derived interpretation.

### 5. Exact ACF bytes remain a first-class raw external-tool output

The parser returns an in-memory `CanonicalBaderIntake` with the exact ACF SHA-256. Durable
materialization requires the caller to provide the exact ACF bytes again and verifies the hash before
writing anything.

The Bader Analysis produces two durable Artifacts:

- `ACF.dat` as `ArtifactType.ACF_DAT`;
- `canonical-bader.json` as `ArtifactType.DERIVED_DATASET`.

Both use `AnalysisProducerRef` and immutable atomic writes under `analyses/<analysis-id>/`.

### 6. Input ownership and scientific identity remain separated from retry identity

The primary CHGCAR must be an exact locally verified Artifact produced by the supplied
ExecutionAttempt, and that ExecutionAttempt must belong to the source Calculation. This validates
runtime provenance.

ExecutionAttempt identity is not included in the scientific source receipt or SCIENTIFIC dependency
DAG. A runtime retry therefore does not become a new scientific dependency merely because the attempt
id changed.

The frozen atom-index map must be produced by the source Calculation. An explicit reference density,
when used, is an additional exact scientific Artifact input.

### 7. The Bader Analysis source receipt is fail-closed

The Analysis `parameters_hash` commits to:

- StructureSnapshot id;
- parser name/version;
- exact ACF SHA-256;
- reference mode;
- complete `ExternalToolInvocation` and its provenance hash;
- ordered scientific input Artifact ids/types/content hashes.

A change in tool version, command, charge density, reference density, ACF output, or parser contract
therefore creates a different scientific identity rather than silently reusing an old Analysis.

### 8. Freshness reuses the existing SCIENTIFIC DAG

The upstream graph is:

`CHARGE_STATIC Calculation -> BADER Analysis`

`CHGCAR Artifact -> BADER Analysis`

`atom-index-map Artifact -> BADER Analysis`

`reference charge-density Artifact -> BADER Analysis` (explicit-reference mode only)

The Analysis then produces SCIENTIFIC edges to `ACF.dat` and the normalized Bader dataset.

No Bader-specific freshness engine is introduced. Existing `FreshnessEngine` propagation is reused.

### 9. Durable reopen validates raw and normalized siblings together

The canonical loader verifies Analysis type/status, both `AnalysisProducerRef` outputs, artifact types,
local bytes/size/SHA-256, payload format/version, Analysis id, sibling ACF id/hash, source receipt hash,
Analysis input ids, and canonical result content hash before reconstructing the value object.

### 10. No schema or dependency change

`SCHEMA_VERSION` remains 3. Existing `AnalysisType.BADER`, `ArtifactType.CHGCAR`,
`ArtifactType.ACF_DAT`, `ArtifactType.DERIVED_DATASET`, `AnalysisProducerRef`, `ProvenanceRecord`, and
`DependencyRecord` contracts are sufficient. No runtime dependency is added.

## Deferred

Block 4 does not add:

- a new VASP recipe with `LAECHG=.TRUE.`;
- AECCAR0/AECCAR2 summation or `chgsum.pl` execution;
- Bader binary execution or scheduler integration;
- oxidation-state or charge-transfer interpretation;
- charge-density-difference subtraction;
- LOBSTER/COHP/ICOHP;
- thermochemistry or electrochemical free-energy analysis.

## Consequences

Bader results become durable, atom-UID-bound electronic analyses with exact external-tool provenance,
while the v0.3 charge-density recipe and v0.6 workflow state model remain unchanged. High-quality
reference-density Bader workflows are representable explicitly, but ECatVASP never fabricates or
silently assumes the reference charge used by the external tool.
