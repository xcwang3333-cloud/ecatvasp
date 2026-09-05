# ADR-052: v0.7 Charge-Density Difference Analysis Boundary

Status: Accepted

## Context

v0.3 already freezes the scientific prerequisite contract for charge-density difference as a strict
three-member `ChargeDifferenceTriplet`: combined adsorbate+slab, frozen slab fragment, and frozen
adsorbate fragment. The prerequisite layer verifies lattice/periodicity, exact atom-UID partition,
frozen coordinates, compatible non-element-specific Method settings, shared-element POTCAR/DFT+U
identity, numerical Protocol settings, neutral members, and spin/MAGMOM subset semantics.

v0.7 must consume the resulting CHGCAR files without inventing a second compatibility model, without
mixing scheduler retry identity into scientific identity, and without confusing VASP's stored CHGCAR
grid scaling with physical electron-number density.

## Decision

### 1. Reuse the frozen v0.3 triplet contract

`AnalysisType.CHARGE_DIFFERENCE` consumes one already-valid `ChargeDifferenceTriplet`. Block 5 does
not duplicate or loosen the v0.3 lattice, structure, Method, POTCAR, DFT+U, Protocol, charge, or
MAGMOM compatibility rules.

All three source Calculations must additionally be scientifically `CONVERGED` before subtraction.
Scheduler success alone is not sufficient.

### 2. Exact CHGCAR provenance is required

Each triplet role (`combined`, `slab`, `adsorbate`) supplies one exact locally available
`ArtifactType.CHGCAR` plus the `ExecutionAttempt` that produced it.

The source guard requires:

- three distinct Calculations, ExecutionAttempts, and CHGCAR Artifacts through the triplet/source
  contracts;
- every CHGCAR producer is the supplied ExecutionAttempt;
- every ExecutionAttempt belongs to the corresponding triplet Calculation;
- exact local byte size and SHA-256 metadata match the bytes read for parsing.

ExecutionAttempt identity is an execution/provenance ownership check only. It is deliberately absent
from the `Analysis` scientific identity and from SCIENTIFIC freshness edges, so an execution-only
retry does not create fake scientific versions when exact scientific inputs are unchanged.

### 3. Parse physical total electron density

The v1 parser uses ASE `VaspChargeDensity` and records the exact ASE version in the source receipt.
ASE converts VASP CHGCAR charge-grid values to physical electron-number density by dividing the
stored grid values by the real-space cell volume. The canonical v1 density unit is therefore
`1/angstrom^3` (electron-number density).

The canonical subtraction is exactly:

`delta_rho = rho_combined - rho_slab - rho_adsorbate`

No sign inversion, normalization, smoothing, interpolation, resampling, or isosurface threshold is
applied during canonical analysis.

For spin-polarized CHGCAR files, v1 uses only the total charge-density grid. The separate
magnetization/spin-density grid is not silently subtracted or interpreted. A future spin-density
analysis requires a separate contract.

### 4. Validate output headers and FFT grids fail closed

The CHGCAR header parsed by ASE must reproduce each member's frozen snapshot in deterministic VASP
POSCAR order:

- exact element/order sequence;
- lattice equal to the frozen snapshot within `1e-9` Angstrom absolute tolerance;
- fractional coordinates equal within `1e-9` absolute tolerance.

The three parsed charge-density arrays must have exactly the same three-dimensional FFT grid shape.
The real-space cell volumes must also agree within `1e-9` Angstrom^3 absolute tolerance. No
interpolation or grid reconciliation is permitted in v1.

### 5. Large volumetric data remains file-backed

The canonical delta-density array is not embedded in a persisted domain row. It is written as an
immutable analysis-produced `DERIVED_DATASET`:

- path: `analyses/<analysis-id>/charge-difference.f64`;
- dtype: little-endian float64 (`<f8`);
- memory order: C order;
- axis contract: `xyz`;
- exact SHA-256 stored in metadata and Artifact provenance.

A sibling `canonical-charge-difference.json` `DERIVED_DATASET` stores only portable metadata and
source receipts, including:

- triplet contract hash;
- FFT grid shape;
- cell and voxel volume;
- physical density unit and subtraction convention;
- density byte SHA-256;
- integrated electron counts of the three inputs and the difference;
- delta-density minimum/maximum;
- ASE parser/version;
- exact input Calculation, StructureSnapshot, MethodFingerprint, Artifact ids, and CHGCAR hashes.

The loader verifies both sibling Artifacts, source-receipt identity, Analysis input ids, metadata
content hash, binary size/hash, finite values, and stored extrema before returning a NumPy array.

### 6. Provenance and freshness reuse existing schema-v3 machinery

The Analysis remains `AnalysisType.CHARGE_DIFFERENCE`; no new persisted entity or schema migration is
introduced.

SCIENTIFIC dependencies are recorded from, for each of the three roles:

- Calculation -> Analysis;
- StructureSnapshot -> Analysis;
- MethodFingerprint -> Analysis;
- exact CHGCAR Artifact -> Analysis.

The Analysis then has SCIENTIFIC edges to the binary density Artifact and metadata Artifact. Existing
`FreshnessEngine` propagation therefore marks the Analysis and both outputs stale if any scientific
upstream hash changes.

`SCHEMA_VERSION` remains 3.

## Consequences

- Charge-density difference is a reproducible scientific Analysis rather than a plotting shortcut.
- Frozen fragment geometry and method compatibility remain owned by the v0.3 prerequisite contract.
- CHGCAR scaling is normalized once into an explicit physical density unit.
- Large three-dimensional arrays stay outside ProjectStore entity rows while remaining immutable,
  content-addressed, reopenable, and freshness-aware.
- Execution retry identity cannot contaminate scientific identity.

## Deferred

This Block does not add:

- interpolation/resampling between incompatible FFT grids;
- spin-density or magnetization-density difference;
- planar/macroscopic averaging, Bader integration, charge-transfer interpretation, isosurfaces, or
  visualization policy;
- ELF or electrostatic-potential analysis;
- new scheduler/backend behavior;
- thermochemistry, CHE, ZPE/entropy corrections, reaction free-energy diagrams, potential, or pH
  corrections;
- tag, GitHub Release, or PyPI publication.
