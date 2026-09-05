# ADR-054: v0.7 Electronic Descriptor Boundary

Status: Accepted

## Context

Blocks 2 and 3 establish canonical DOS/PDOS facts in the native VASP energy frame and materialize
those facts as immutable, file-backed `DERIVED_DATASET` Artifacts. The canonical parser intentionally
keeps atom-resolved lm projections and does not persist element aggregation or electronic descriptors.

v0.7 Block 7 must derive band centers, p-band centers, and d-band centers without mutating canonical
DOS facts, silently shifting energy zero, conflating spin channels, clipping projected DOS values, or
turning a descriptor into an unversioned property of a structure or calculation.

## Decision

### 1. Descriptors are `BAND_CENTER` Analyses

Band center, p-band center, and d-band center are all durable `AnalysisType.BAND_CENTER` operations.
They consume one exact canonical DOS Artifact produced by a completed `AnalysisType.DOS` Analysis and
produce one immutable `canonical-band-center.json` `DERIVED_DATASET` through `AnalysisProducerRef`.

No descriptor value is written onto `StructureSnapshot`, `Calculation`, or the canonical DOS result.

### 2. Scientific identity is fully parameterized

One descriptor is identified by the exact upstream canonical DOS Artifact plus all numerical and
selection parameters. The source receipt and Analysis parameters hash include:

- exact source DOS Artifact id and SHA-256;
- exact canonical DOS content hash;
- descriptor kind (`band`, `p_band`, or `d_band`);
- projection scope and permanent atom/element selector;
- explicit spin mode;
- explicit energy reference;
- lower and upper integration-window bounds;
- integration rule;
- normalization convention;
- descriptor implementation tool and version through the Analysis contract.

Changing any of these values creates a distinct scientific Analysis identity.

### 3. Projection scopes are explicit

`BandCenterSelector` reuses the existing `ProjectionScope` vocabulary:

- `SYSTEM`: only generic band center is allowed because system DOS has no lm decomposition;
- `ATOM`: selects one exact permanent `atom_uid` plus its element label;
- `ELEMENT`: deterministically aggregates all matching atom-projected channels for that element.

Element aggregation is a downstream operation. It does not introduce persisted element-PDOS facts and
does not alter `CanonicalDosResult`.

For p-band and d-band centers, selected atom-projected channels are filtered by canonical orbital
angular momentum `l=1` and `l=2`, respectively. All matching magnetic-quantum-number channels are
summed exactly once. No geometry inference or nearest-neighbour identity mapping is permitted.

For a generic atom/element band center, lm-resolved channels are summed exactly once. If atom-total and
orbital-resolved projections coexist in a way that makes such summation ambiguous, v1 fails closed
rather than choosing one representation silently.

### 4. Spin selection never changes meaning implicitly

Descriptor spin mode is one of:

- `TOTAL` for canonical unpolarized total DOS;
- `UP` for the collinear up channel;
- `DOWN` for the collinear down channel;
- `SUM` for an explicit up-plus-down aggregation.

`TOTAL` is not an alias for `UP + DOWN`. A spin-polarized source therefore rejects `TOTAL`; callers
must explicitly request `UP`, `DOWN`, or `SUM`. Conversely, an unpolarized source accepts only
`TOTAL`.

### 5. Energy reference is explicit

Canonical DOS remains in `ElectronicEnergyReference.VASP_NATIVE`. Block 7 allows two descriptor views:

- `VASP_NATIVE`: use canonical VASP energies directly;
- `FERMI_RELATIVE`: use the explicit `E - E_F` view returned by the canonical energy-axis contract.

The requested integration window is expressed in the chosen frame, and the reported center uses that
same frame. No implicit zero shift is performed.

### 6. Numerical integration rule is frozen for v1

The descriptor is the DOS-weighted first moment

`epsilon = integral(E * rho(E) dE) / integral(rho(E) dE)`.

v1 uses `TRAPEZOID_LINEAR_ENDPOINTS`:

1. the requested window must lie entirely inside the canonical DOS energy range;
2. canonical grid points strictly inside the window are retained;
3. if a window endpoint is not already a canonical grid point, its DOS value is obtained by linear
   interpolation between the two neighboring canonical points;
4. trapezoidal quadrature is then applied to `rho(E)` and to `E * rho(E)` on that clipped point set.

This endpoint interpolation is part of the descriptor quadrature rule. It does not resample, rewrite,
or replace the upstream canonical DOS dataset. Extrapolation outside the source energy range is
forbidden.

The normalization convention is `DOS_WEIGHTED_FIRST_MOMENT`. The zeroth DOS moment must be strictly
positive. v1 does not take an absolute value, clip negative PDOS values, smooth data, renormalize
channels, or silently apply occupancy weights. A non-positive zeroth moment fails closed.

### 7. Durable result records auditable moments

The canonical descriptor result stores:

- immutable StructureSnapshot id inherited from the source DOS facts;
- complete descriptor parameters;
- reported center in eV;
- zeroth DOS moment;
- first energy-weighted DOS moment;
- quadrature point count;
- contributing canonical series count;
- exact source canonical-DOS content hash;
- exact source Artifact SHA-256.

The loader reopens and validates the source canonical DOS Artifact, validates the descriptor source
receipt and output Artifact, recomputes the descriptor from the exact source facts, and rejects any
stored result that differs from that recomputation.

### 8. Freshness reuses the existing scientific DAG

The descriptor Analysis has a direct SCIENTIFIC dependency on the exact canonical DOS Artifact, and
the descriptor Artifact depends scientifically on the descriptor Analysis.

The existing DOS Analysis -> canonical DOS Artifact edge already connects the descriptor to upstream
DOSCAR, atom-index-map, and Calculation dependencies. Block 7 therefore does not duplicate the full
raw-DOS dependency set and does not introduce another freshness engine.

### 9. Schema remains v3

The existing generic `Analysis`, `Artifact`, provenance, and dependency records express the complete
Block 7 lifecycle. No new persisted entity is required and `SCHEMA_VERSION` remains 3.

## Consequences

- Descriptor values are reproducible operations over immutable DOS facts rather than mutable
  annotations.
- p/d-band centers cannot accidentally include the wrong angular-momentum channels.
- atom and element selection remains anchored to permanent atom identity and exact parsed facts.
- spin summation and Fermi referencing are visible scientific choices.
- the same canonical DOS Artifact can support multiple windows/selectors without changing or copying
  its canonical facts.
- source Artifact drift propagates stale state through the existing SCIENTIFIC DAG.

## Deferred

Block 7 does not add:

- orbital centers from COHP/ICOHP data;
- center calculations for noncollinear/SOC magnetization components;
- vacuum-level alignment or work-function analysis;
- band-structure path descriptors, Wannier centers, ELF interpretation, COOP, or COBI;
- occupancy-weighted or user-defined arbitrary mathematical transforms;
- plotting, GUI, or visualization policy;
- thermochemistry, ZPE, entropy, gas references, CHE, potential/pH corrections, or free-energy
  diagrams;
- a new scheduler/backend or persisted workflow state machine;
- tag, GitHub Release, or PyPI publication.
