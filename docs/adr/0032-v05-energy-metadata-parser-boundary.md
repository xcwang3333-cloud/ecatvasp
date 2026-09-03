# ADR-032: v0.5 Energy and Metadata Parser Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

ADR-030 defines the normalized `VaspResultDocument` and explicitly separates parsed facts from
recipe-aware convergence assessment. ADR-031 then establishes a content-addressed,
ExecutionAttempt-specific intake so a parser does not discover files by directory scanning or trust
stale Artifact metadata.

Block 3 is the first concrete managed-result parser. Its job is intentionally narrower than the
legacy v0.1 existing-folder importer: extract explicit VASP energy semantics and lightweight run
metadata without assigning scientific success, updating Calculation state, or accepting a relaxed
structure.

## Decision

### 1. Block 3 consumes only a Block 2 intake

`parse_vasp_energy_metadata()` accepts a `VaspResultArtifactIntake` plus the project root. It does
not accept arbitrary OUTCAR paths, scan execution directories, synthesize Artifacts, or bypass the
Block 2 source contract.

All admitted files are re-resolved below the project root and re-hashed at parser time. This closes
the time-of-check/time-of-use gap between intake construction and scientific parsing. CONTCAR and
an explicitly admitted `vasprun.xml` remain uninterpreted in this block, but their bytes are still
re-verified because the resulting document preserves the exact intake source bundle.

### 2. OUTCAR owns explicit energy semantics

OUTCAR is authoritative for the initial normalized energy fields:

- `free_energy_toten_ev` from `free energy TOTEN`;
- `energy_without_entropy_ev` from `energy without entropy`;
- `energy_sigma0_ev` from `energy(sigma->0)`;
- `fermi_energy_ev` from `E-fermi`.

For repeated ionic/electronic output, the latest observed valid value is retained. No generic
`total_energy_ev` alias is introduced.

A missing marker is represented by `None`. If a marker is present but its numeric payload is not
parseable, the value remains `None` and an explicit `*_unparseable` evidence code is emitted. The
parser never substitutes another energy convention.

### 3. Lightweight metadata remains source-specific

OUTCAR also supplies:

- the VASP version header;
- whether the normal timing/accounting termination marker was observed;
- raw observation codes for the electronic `EDIFF is reached` marker;
- raw observation codes for the ionic `reached required accuracy` marker.

These are observations only. They are not `ConvergenceVerdict` values.

OSZICAR supplements step metadata when it is present in the intake:

- `ionic_steps` is the number of observed ionic summary rows matching the VASP `N F=` form;
- `electronic_steps` is the latest observed electronic iteration index from DAV/RMM/CG/DMP rows.

No OUTCAR fallback is invented for missing OSZICAR step metadata.

### 4. Evidence codes are stable parser observations

`VaspParserEvidenceCode` provides machine-readable observation codes for extracted values,
termination, raw convergence markers, OSZICAR step metadata, and unparseable numeric markers.

The codes deliberately do not encode `converged`, `unconverged`, scientific status, retry advice,
or continuation policy. Block 4 will interpret appropriate observations using calculation-type and
recipe-aware convergence rules.

### 5. Ambiguous or drifting sources fail closed

Parsing fails when:

- a source resolves outside the project root;
- an admitted file disappears;
- byte size or SHA-256 no longer matches the intake;
- one OUTCAR contains multiple distinct VASP version headers.

Partial or failed calculations are still parseable when their admitted files are intact. They may
therefore yield a valid `VaspResultDocument` with missing energies and
`termination_observed=False`.

### 6. No durable Analysis/Artifact write yet

Block 3 returns the normalized value document only. It does not yet create or complete a
`RESULT_PARSE` Analysis, persist a `PARSED_RESULT` Artifact, mutate an ExecutionAttempt to PARSED,
or write provenance/dependency records. Those durable lifecycle operations remain separate from
the concrete text parser.

### 7. Legacy importer is reference behavior, not the new parser backend

The v0.1 importer already contains regex-based extraction for TOTEN, Fermi energy, VASP version,
step counts, and convergence booleans. Block 3 does not call that private parser because it also
assigns `CalculationScientificStatus` and exposes an ambiguous `total_energy_ev` field.

The legacy importer remains unchanged for compatibility while the managed v0.5 pipeline adopts the
new explicit contract.

## External implementation references

The parser boundary was cross-checked against established open-source projects:

- `materialsproject/pymatgen` distinguishes VASP free energy, energy without entropy, and
  sigma-to-zero energy in its VASP output handling and test fixtures rather than collapsing them
  into one universal energy field:
  <https://github.com/materialsproject/pymatgen>
- `materialsproject/atomate2` documents VASP calculation outputs through a stable task-document
  schema, reinforcing the separation between execution and normalized scientific output:
  <https://github.com/materialsproject/atomate2>

ECatVASP does not import either project's workflow/database model or add either project as a runtime
dependency.

## Non-scope

Block 3 does not add:

- convergence classification or `CalculationScientificStatus` mutation;
- RESULT_PARSE Analysis lifecycle or PARSED_RESULT persistence;
- force, stress, magnetization, eigenvalue, DOS, PDOS, or band parsing;
- frequency-mode extraction;
- CONTCAR geometry/atom-UID reconstruction or structure promotion;
- vasprun.xml scientific parsing or fallback substitution;
- retrieval, retention, restart, correction, or continuation logic;
- thermochemistry, CHE, workflow orchestration, GUI work, schema migration;
- tag, GitHub Release, or PyPI publication.

## Consequences

Managed VASP results now have a deterministic concrete parser for the energy and metadata facts
needed by later scientific logic. Block 4 can classify convergence from normalized raw evidence
without rereading scheduler state or inheriting the legacy importer’s scientific-status coupling.
