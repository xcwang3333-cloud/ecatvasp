# ADR-030: v0.5 Scientific Result Parsing Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

v0.4 ends at a verified execution handoff. ADR-029 deliberately prevents scheduler completion,
`ExecutionAttempt.EXITED`, retrieval, or execution acceptance from being promoted into a scientific
convergence claim. The next layer therefore needs an explicit scientific-result contract before
workflow orchestration, electronic-structure analyses, or thermochemistry can consume VASP output.

ECatVASP already contains a conservative v0.1 existing-folder importer with a small
`ParsedVaspResult` vertical slice. That legacy importer extracts selected energies, force summary,
step counts, VASP version, and convergence markers, and it may immediately update a
`StructureVariant` to an imported CONTCAR. v0.5 must preserve compatibility with that importer
while establishing a stricter permanent boundary for newly managed results.

The frozen Domain already provides `Analysis`, `Artifact`, provenance/freshness records,
`CalculationScientificStatus`, and `ArtifactType.PARSED_RESULT`. A new top-level
`CalculationResult` entity would duplicate these abstractions and require unnecessary storage
migration.

## Decision

### 1. Result parsing is an Analysis

`AnalysisType.RESULT_PARSE` identifies the durable scientific operation that consumes exact raw
VASP Artifacts and produces normalized parsed-result data. Result parsing is not a VASP
`Calculation`, `ExecutionAttempt`, scheduler state, or workflow node family.

A result-parse Analysis requires explicit parser `tool` and `tool_version` provenance. Parser
implementation details remain behind the VASP/external-tool adapter boundary from ADR-009.

### 2. Raw scientific outputs remain content-addressed inputs

`VaspResultSource` binds one source role to an exact Artifact id, Artifact type, and SHA-256 digest.
The initial stable source vocabulary is:

- OUTCAR;
- OSZICAR;
- CONTCAR;
- vasprun.xml.

The normalized result contract requires OUTCAR as the canonical minimum source. Optional source
roles may later provide additional evidence or cross-checks, but a parser must not silently replace
one source identity with another.

Block 1 defines source identity only. Retrieval, local availability, exact source-set intake, and
recipe-dependent CONTCAR requirements are deferred to Block 2.

### 3. Energy semantics are explicit

`VaspEnergySummary` does not expose a generic `total_energy_ev`. The contract distinguishes:

- VASP `free energy TOTEN`;
- energy without entropy;
- sigma-to-zero energy;
- Fermi energy.

This prevents downstream adsorption-energy or thermochemistry code from accidentally treating one
VASP energy convention as a universal total energy.

### 4. Parsed facts and convergence verdicts are separate

`VaspResultDocument` contains parser-normalized facts only: exact sources, calculation type, energy
fields, VASP version, step counts, termination observation, and machine-readable evidence codes.
It deliberately contains no `CalculationScientificStatus`, `electronic_converged`,
`ionic_converged`, or generic success flag.

`VaspConvergenceAssessment` is a separate value contract using four verdicts:

- `CONVERGED`;
- `UNCONVERGED`;
- `INDETERMINATE`;
- `NOT_APPLICABLE`.

Block 4 will define recipe-aware convergence rules. Parser evidence may inform that assessment but
cannot itself mutate a Calculation lifecycle state.

The intended durable chain is:

`ExecutionAttempt-produced raw Artifacts -> RESULT_PARSE Analysis -> PARSED_RESULT Artifact -> CONVERGENCE Analysis -> Calculation scientific-status reconciliation`.

### 5. Parsing does not promote structures

CONTCAR is a raw execution Artifact and may later be reconstructed as an immutable relaxed
`StructureSnapshot`. Result parsing alone must not modify
`StructureVariant.current_structure_snapshot_id`, create a continuation Calculation, or imply that
an unconverged geometry is accepted as the current scientific structure.

Block 6 will define deterministic atom-UID reconstruction and explicit promotion.

### 6. No storage-schema migration

The project schema remains version 2. `AnalysisType` is already persisted through the generic enum
codec, and `Analysis`, `Artifact`, `ProvenanceRecord`, and `DependencyRecord` are already supported
by `ProjectBundle`. The new parser-neutral value objects are serialized inside future result
artifacts rather than becoming new ProjectBundle entity families.

### 7. Legacy importer remains compatible during the transition

Block 1 does not remove or reinterpret the v0.1 `ParsedVaspResult` importer surface. Its current
behavior remains compatibility code while Blocks 2-8 move both managed execution results and
existing-folder imports onto the normalized v0.5 scientific-result pipeline.

No existing persisted project is rewritten by this block.

## External implementation references

The boundary was cross-checked against established open-source VASP workflow projects without
importing their domain models:

- atomate2 parses VASP outputs into a task document that is distinct from job/workflow execution;
- aiida-vasp keeps parser output/convergence handling explicit and separate from CalcJob execution
  state, including distinct ionic-convergence checks.

ECatVASP retains a smaller immutable Analysis/Artifact model and does not add either project's
workflow or database abstractions.

## Non-scope

Block 1 does not add:

- concrete OUTCAR, OSZICAR, vasprun.xml, or CONTCAR parsing;
- recipe-aware convergence classification;
- Calculation scientific-status mutation;
- force or magnetization datasets;
- frequency-mode extraction;
- CONTCAR atom-UID reconstruction or structure promotion;
- result retrieval or source-file movement;
- DOS, PDOS, Bader, charge-density difference, COHP, or LOBSTER execution;
- ZPE, entropy, CHE, or free-energy workflows;
- scientific workflow orchestration;
- automatic correction, restart, or continuation;
- GUI changes;
- schema migration;
- a tag, GitHub Release, or PyPI publication.

## Consequences

v0.5 can now grow result intake, concrete parsing, convergence classification, structure promotion,
and provenance as separate blocks without weakening the v0.4 execution boundary. Downstream
scientific workflow and thermochemistry phases will consume explicit scientific results rather
than scheduler state or ad hoc importer booleans.
