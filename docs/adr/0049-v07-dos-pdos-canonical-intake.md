# ADR-049: v0.7 DOS/PDOS Canonical Intake Boundary

- Status: Accepted
- Date: 2026-09-04
- Scope: v0.7 Block 2

## Context

ADR-048 defines native-axis DOS/PDOS facts, permanent atom identity, explicit spin semantics, and the
separation between VASP `DOS_STATIC` Calculations and downstream DOS/PDOS Analyses. Block 2 must now
normalize DOSCAR data without weakening the frozen v0.3 atom-index-map boundary or prematurely adding
durable Analysis/Artifact materialization, which belongs to Block 3.

The v0.3 `DOSPrerequisite` recipe already fixes `LORBIT=11` and explicit `NEDOS >= 2`, so a managed
DOS result is expected to contain total DOS plus site- and lm-resolved projected DOS.

## Decision

### 1. DOSCAR is the initial canonical parser source

Block 2 supports exact VASP DOSCAR bytes. The raw DOSCAR remains authoritative as an Artifact; parsing
does not replace or mutate it. `vasprun.xml`, `vaspout.h5`, py4vasp, pymatgen, and other alternate
parser sources are not mixed into this Block because cross-source reconciliation would create a second
scientific question.

The parser records the exact DOSCAR SHA-256 in `CanonicalDosIntake`. Block 3 will bind that digest to
the exact managed DOSCAR Artifact and create durable Analysis/provenance records.

### 2. Native VASP energy semantics remain canonical

The DOSCAR energy grid is preserved exactly as parsed, and the DOSCAR Fermi level is stored separately
in the same native energy frame. No implicit `E-E_F` shift occurs. Integrated-DOS columns are validated
as part of the supported total-DOS row shape but are not copied into canonical DOS contract v1; the raw
DOSCAR remains retained if integrated DOS is needed later.

### 3. Spin schema is not guessed from columns alone

The caller supplies the frozen Method `SpinTreatment`. The parser requires the DOSCAR total-DOS layout
to agree with it:

- unpolarized: `energy, DOS, integrated DOS`;
- collinear: `energy, DOS(up), DOS(down), integrated DOS(up), integrated DOS(down)`.

SOC and noncollinear calculations fail closed. Block 2 does not relabel noncollinear magnetization
components as collinear up/down DOS.

### 4. LORBIT=11 orbital order is explicit

For the initial s/p/d contract, lm-resolved channels are normalized in VASP order:

`s, py, pz, px, dxy, dyz, dz2, dxz, dx2-y2`.

In collinear DOSCAR data, up/down values immediately follow one another for each orbital. Supported
orbital counts are therefore 1, 4, or 9 per atom. Any other column count fails closed.

The current parser deliberately does not invent labels for f-channel real spherical harmonics. A
future extension may add a separately tested f-orbital label contract. Until then, a 16-orbital
LORBIT=11 block is rejected rather than ambiguously named.

### 5. Permanent atom identity comes only from the frozen atom map

The parser accepts the exact `atom-index-map.json` bytes and requires:

- `ecatvasp-v03-atom-index-map`, version 1;
- exact `structure_snapshot_id` match;
- valid structure/POSCAR SHA-256 metadata;
- contiguous `poscar_index` and one-based `vasp_ordinal`;
- unique valid permanent `atom_uid` values;
- nonblank element labels;
- `species_order/species_counts` exactly reproducing entry order.

The DOSCAR ion count must equal the map size. No position, distance, species-only, or nearest-neighbour
fallback is permitted.

### 6. Total and projected grids must be identical

Every site block must repeat the total block's `NEDOS`, `EMAX`, `EMIN`, and Fermi level and must use the
same point-by-point energy grid. Numerical comparison is limited to a small absolute tolerance for
text-to-float representation; the parser does not interpolate or resample.

Missing site blocks, extra non-empty trailing blocks, malformed rows, unsupported empty-sphere
indexing, absent projected DOS, and inconsistent grids all fail closed.

### 7. Block 2 remains parser-only

Block 2 creates immutable in-memory `CanonicalDosIntake` / `CanonicalDosResult` values only. It does
not:

- create or mutate `Analysis` entities;
- write normalized datasets to ProjectStore;
- create `AnalysisProducerRef` Artifacts;
- register freshness dependencies;
- choose element/orbital aggregations or energy windows;
- calculate band/d-band/p-band centers;
- alter v0.6 workflow state.

Those boundaries are addressed in Blocks 3, 7, and 8.

### 8. No schema or dependency change

`SCHEMA_VERSION` remains 3. No runtime dependency is added. The implementation uses only the Python
standard library and the v0.7 Block 1 contracts.

## Consequences

Block 2 provides one deterministic, inspectable DOSCAR normalization path that is strict enough for
later durable provenance. It favors rejection over silent reinterpretation whenever the DOSCAR layout,
spin model, atom identity, or orbital labeling cannot be established exactly.
