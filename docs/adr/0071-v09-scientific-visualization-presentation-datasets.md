# ADR-071: v0.9 Scientific Visualization Presentation Datasets

- Status: Accepted
- Date: 2026-09-07

## Context

ADR-067 defines v0.9 as Research Workspace and Scientific Presentation. Blocks 1 through 3 now
provide stable read boundaries for project summary, provenance/freshness inspection, and exact
workflow/execution readiness. The next gap is a stable contract that frontend, notebook, and reporting
clients can use to render existing scientific results without reading parser internals or becoming a
second scientific authority.

ECatVASP already has mature canonical sources:

- `StructureSnapshot` plus the identity-preserving MatterViz adapter;
- `CanonicalDosResult` for DOS/PDOS on the native VASP energy frame;
- `CanonicalCohpResult` for native-sign LOBSTER COHP/ICOHP on a Fermi-relative energy grid; and
- `ReactionDiagramDataset` for durable, plotting-neutral electrocatalytic pathway data at explicit
  CHE conditions.

Block 4 must make those sources presentation-ready without adding a plotting engine, recomputing
scientific values, inferring semantics from labels/files, or persisting UI style as provenance.

## Decision

### 1. Presentation DTOs are transient and versioned

Block 4 adds `ecatvasp.visualization.presentation` and the presentation contract version:

`ecatvasp-scientific-presentation-v1`

Presentation datasets are immutable application/view DTOs. They are not added to `ProjectBundle`, do
not become provenance subjects, do not create dependency records, and are not scientific hashes of
their own. They retain exact source hashes so clients can trace which canonical scientific result was
projected.

`SCHEMA_VERSION` remains 3 and package version remains `0.9.0.dev0`.

### 2. Structure presentation composes the existing MatterViz adapter

Block 4 does not duplicate structure serialization or atom-index semantics. `StructurePresentationDataset`
wraps the existing `MatterVizViewBundle` together with the exact `StructureSnapshotId` and canonical
`scientific_hash(StructureSnapshot)`.

The existing MatterViz guarantees remain authoritative:

- permanent `atom_uid` to viewer-index mapping;
- original-cell / 1x1x1 overlay frame;
- no image-atom identity guessing; and
- identity-preserving extXYZ fallback when the interactive runtime is unavailable.

MatterViz remains a presentation adapter. JavaScript/runtime availability never changes scientific
payload generation.

### 3. DOS/PDOS presentation preserves canonical values

`build_dos_presentation()` consumes one `CanonicalDosResult` and exposes:

- exact source `content_hash`;
- exact `StructureSnapshotId` and atom-index-map SHA-256;
- the native VASP energy grid;
- the explicit Fermi energy;
- an `E - E_F` view derived only through the canonical `ElectronicEnergyAxis.relative_to_fermi()`;
- exact system/atom/orbital selectors and spin channel; and
- unchanged DOS/PDOS numerical values.

The presentation layer does not aggregate element PDOS, smooth curves, mirror/down-sign spin series,
clip energy windows, normalize intensities, or choose plotted labels/colors.

The display units are explicit: energy in eV and DOS density in states/eV.

### 4. COHP/ICOHP presentation preserves LOBSTER native sign

`build_cohp_presentation()` consumes one `CanonicalCohpResult` and retains:

- exact source `content_hash`;
- Fermi-relative energy grid and source Fermi energy;
- frozen atom-index-map SHA-256;
- average and pair/orbital spin series;
- permanent pair `atom_uid` identity;
- element, orbital, periodic-cell, and bond-length metadata; and
- native COHP and integrated-COHP values including explicit `ICOHP(E_F)` when present.

The presentation contract records `sign_convention = "lobster_native"`. It does not apply `-COHP`,
bonding/antibonding labels, thresholds, interpolation, or bond-strength classifications. A UI may
choose a conventional `-COHP` rendering only as a client-side display transform and must not rewrite
the source DTO.

### 5. Reaction-diagram presentation copies canonical CHE/pathway state

`build_reaction_diagram_presentation()` consumes one `ReactionDiagramDataset`. It does not invoke CHE
or reaction/pathway evaluators. It projects exactly:

- source diagram `result_hash` and potential-view `result_hash`;
- pathway-definition and baseline-result hashes;
- baseline and requested CHE temperature, potential, pH, reference-electrode, pH semantics, and
  condition hashes;
- ordered state keys and cumulative free energies;
- ordered adjacent step keys, explicit CHE coefficients, affine potential slopes, baseline free
  energies, and requested-condition free energies;
- canonical descriptor definitions and units; and
- exact durable non-CHE source receipts with Analysis/Artifact identifiers and hashes.

Free-energy unit is eV and potential unit is V. State/step ordering comes only from the canonical
`PotentialDependentPathwayView`; presentation code does not infer reaction order from names.

### 6. Style is explicitly outside the scientific contract

The Block 4 DTOs contain no scientific authority for:

- colors or palettes;
- line width/style;
- axis ranges, clipping, smoothing, normalization, or interpolation;
- camera position or structure-view controls;
- panel layout;
- legend placement;
- typography; or
- export format.

Those choices may exist in downstream UI/report code, but they are not persisted scientific state and
must never influence source hashes, freshness, workflow gates, or analysis validity.

### 7. Fail closed rather than reinterpret malformed sources

Presentation DTO construction validates contract version, SHA-256 fields, common series-grid lengths,
fixed scientific sign conventions, and fixed scientific units. Canonical source constructors continue
to own deeper scientific validation. Block 4 never repairs or guesses inconsistent canonical input.

## Acceptance criteria

Block 4 is accepted when:

- structure presentation reuses the current MatterViz adapter and preserves exact snapshot and
  permanent atom identity;
- DOS/PDOS values are unchanged while native and explicit Fermi-relative energy axes are available;
- no element aggregation, smoothing, clipping, normalization, or spin-sign display transform is
  performed;
- COHP/ICOHP native sign and exact permanent pair/orbital identity are preserved;
- no `-COHP` scientific canonicalization or bonding interpretation is introduced;
- reaction-diagram states, steps, CHE conditions, descriptors, result hashes, and source receipts are
  copied from the canonical dataset without CHE/pathway recomputation;
- presentation dictionaries contain no style authority;
- no presentation DTO is persisted or inserted into provenance/dependency/freshness state;
- `SCHEMA_VERSION` remains 3, package remains `0.9.0.dev0`, and no runtime dependency is added; and
- Ruff, mypy strict, pytest on Python 3.11/3.12/3.13, and the MatterViz contract remain green.

## Consequences

Block 5 can build deterministic JSON/CSV/Markdown scientific reports over stable source-linked
presentation datasets rather than reaching into parser-specific structures. Block 8 can serialize the
same presentation contract for frontend/desktop handoff without making frontend state scientific
authority.

Block 4 deliberately does not add a plotting library. Actual chart styling and interactive rendering
remain downstream presentation concerns.