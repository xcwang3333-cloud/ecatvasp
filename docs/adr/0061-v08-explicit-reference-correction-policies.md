# ADR-061: v0.8 Explicit Reference and Correction Policies

- Status: Accepted
- Date: 2026-09-05

## Context

ADR-059 and ADR-060 deliberately produce raw, component-resolved thermochemistry without empirical,
experimental, phase-change, solvation, or DFT-reference corrections. That separation is necessary
because electrocatalysis workflows often use corrections whose value and applicability depend on a
chosen literature protocol, experimental reference, calibration, phase convention, or user-declared
method.

Block 4 must therefore support O2/reference corrections and water phase-reference strategies without
turning any correction into a hidden project default and without rewriting the raw harmonic or
ideal-gas parent result.

## Decision

### 1. Corrected references are derived datasets, not mutated raw thermochemistry

Block 4 introduces `ReferenceThermochemistryResult`.

It records:

- the exact raw `ThermochemistryResult.result_hash`;
- the raw Gibbs free energy;
- the molecular reference identity;
- a target physical reference phase;
- every explicit correction policy and its evidence;
- the corrected Gibbs free energy;
- a deterministic result hash.

The corrected value is evaluated only as:

`G_reference = G_raw + sum(C_i)`.

The raw electronic, ZPE, thermal-energy, entropy, pV, and mode-selection facts remain in the Block 3
parent dataset and are not rewritten or collapsed into the corrected result.

### 2. O2 has no automatic correction

ECatVASP does not embed an O2 overbinding number, infer one from the XC functional, select one from a
literature table, or apply one because the species name is O2.

An O2 correction is present only when the user supplies an explicit `ThermochemistryCorrection`,
for example with kind `DFT_REFERENCE`, together with a versioned evidence record. Different values,
policy versions, or evidence versions produce different Analysis identities.

The same rule applies to H2, H2O, CO, CO2, and future references.

### 3. Correction evidence is part of scientific identity

`CorrectionEvidence` records:

- evidence kind: user-declared, literature, experimental, or calibration;
- stable source id;
- source version;
- optional citation text;
- optional exact project Artifact id and SHA-256.

`ReferenceCorrectionPolicy` binds one `ThermochemistryCorrection` to one evidence record. The
complete policy/evidence object participates in `GasReferenceAdjustmentIdentity.parameters_hash`.
Therefore an unchanged numerical correction with a different source or source version is still a
scientifically distinct Analysis.

When a correction is backed by a project Artifact, materialization requires that exact Artifact and
SHA-256 and creates a SCIENTIFIC dependency. User-declared or external textual evidence that has no
project Artifact remains fully recorded in Analysis parameters rather than being represented by a
fake Artifact.

### 4. Evidence Artifacts are optional but exact when declared

A correction evidence record may omit `artifact_id`/`artifact_sha256` entirely. If either is supplied,
both are required.

During materialization, the provided evidence Artifact set must exactly equal the set referenced by
all policies. Missing, extra, or hash-mismatched evidence fails closed. Multiple policies may share
the same Artifact only when they declare the same content hash.

### 5. The initial target-phase vocabulary is intentionally narrow

Block 4 supports:

- `IDEAL_GAS`: preserves the raw Block 3 gas reference phase;
- `LIQUID_WATER`: a liquid-water reference derived from an H2O ideal-gas parent.

`LIQUID_WATER` is valid only for H2O and requires exactly one explicit `PHASE_CHANGE` correction.
An ideal-gas target forbids phase-change corrections.

Block 4 does not silently implement an aqueous-solute 1 M standard state. CO2(aq), dissolved CO, or
other aqueous-solute references require an explicit concentration/standard-state convention and are
deferred rather than approximated by a generic solvation number.

### 6. Other correction kinds remain visible and additive

The existing correction vocabulary remains available:

- `DFT_REFERENCE`;
- `EXPERIMENTAL_REFERENCE`;
- `PHASE_CHANGE`;
- `SOLVATION`;
- `USER_DECLARED`.

Block 4 does not assign default meanings or values to these kinds. They are typed audit labels around
an explicit signed additive energy and a versioned policy identity.

### 7. Double correction is rejected

`apply_reference_corrections()` accepts only a raw gas thermochemistry parent whose identity and
components contain no pre-existing corrections. A corrected `ThermochemistryResult` cannot be fed
back into Block 4 and corrected again.

This keeps each correction layer single, explicit, and inspectable.

### 8. Durable provenance uses the existing scientific DAG

Materialization requires a completed Block 3 `THERMOCHEMISTRY Analysis` and its exact locally
available `DERIVED_DATASET Artifact`. The source Artifact is reopened and checked for:

- project-relative path confinement;
- byte size and SHA-256;
- canonical Block 3 format/version;
- source Analysis id;
- source-receipt hash agreement with the Analysis;
- molecular reference identity;
- raw thermochemistry result hash and canonical content.

The derived correction Analysis is also `AnalysisType.THERMOCHEMISTRY`; it uses tool identity
`ecatvasp.thermo.reference-correction` and produces a canonical `DERIVED_DATASET`.

SCIENTIFIC dependencies are:

- raw gas Thermochemistry Analysis -> correction Analysis;
- raw gas Thermochemistry Artifact -> correction Analysis;
- each declared evidence Artifact -> correction Analysis;
- correction Analysis -> corrected reference Artifact.

Existing transitive FreshnessEngine semantics therefore invalidate corrected references when raw gas
thermochemistry or exact evidence Artifacts drift.

### 9. No correction database is introduced

Block 4 does not create a built-in numerical correction registry, scrape literature values, or claim
that one correction is universally appropriate for PBE, RPBE, SCAN, hybrids, solvation models, or
other methods. Scientific policy selection remains explicit and user-auditable.

### 10. Schema and dependency boundaries remain unchanged

`SCHEMA_VERSION` remains 3. No new runtime dependency is added. The reference/correction layer is
expressed entirely with existing Analysis, Artifact, provenance, dependency, and canonical hashing
infrastructure.

## Consequences

ECatVASP can now represent corrected O2 or other gas references and a liquid-water reference without
polluting the raw thermochemistry layer. Every additive value has an explicit sign, policy id/version,
evidence identity, and optional content-addressed evidence dependency. CHE remains downstream in
Block 5 and cannot hide reference corrections inside electrode-potential semantics.
