# ADR-035: v0.5 CONTCAR Reconstruction and Structure Promotion Boundary

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-031 admits exact retrieved VASP outputs, ADR-033 produces scientific convergence verdicts,
and ADR-034 binds final per-atom observables to permanent atom identity. Block 6 must now turn an
exact retrieved CONTCAR into a new immutable `StructureSnapshot` without collapsing three distinct
operations into one:

1. reading a CONTCAR;
2. reconstructing a candidate structure revision;
3. changing a `StructureVariant`'s current snapshot.

The legacy existing-folder importer performs these steps together and immediately points the variant
to the imported final structure. That behavior is intentionally not reused for the managed v0.5
result pipeline.

## Decision

### 1. Reconstruction and promotion are separate public operations

`reconstruct_vasp_contcar_snapshot()` creates an immutable candidate snapshot from one exact result
intake. `promote_vasp_contcar_snapshot()` is a second, explicit operation that may update only the
returned `StructureVariant` value.

Neither function persists project storage or creates a new Calculation.

### 2. Reconstruction requires an exact relax Calculation and exact result intake

CONTCAR reconstruction is limited to `RELAX` and `GAS_RELAX` calculations. The Calculation,
ExecutionPlan, result intake, recipe, CalculationType, plan hash, and input-manifest hash must agree.
The exact input `StructureSnapshot` must equal `Calculation.input_structure_snapshot_id`.

### 3. Permanent atom identity comes from the staged atom-index map

The exact staged `atom-index-map.json` and POSCAR are revalidated by byte size and SHA-256 before
use. The map must:

- use the supported v0.3 format/version;
- reference the exact input snapshot;
- bind the exact staged POSCAR SHA-256;
- contain the same number of atoms as the input snapshot;
- use contiguous POSCAR indices and VASP ordinals;
- use snapshot indices that form a permutation of the input snapshot;
- carry `atom_uid` and element identities that exactly match those immutable source sites.

The exact staged POSCAR is parsed and its VASP element order must match the map. No geometric,
nearest-neighbour, tolerance, or element-only identity matching is permitted.

### 4. The candidate uses CONTCAR geometry and CONTCAR lattice

The exact retrieved CONTCAR is revalidated by byte size and SHA-256 at reconstruction time. The
candidate:

- uses the CONTCAR lattice, including variable-cell relax results;
- uses CONTCAR fractional geometry;
- preserves permanent `atom_uid` through exact VASP ordinal order;
- requires exact atom count/species/order agreement with the staged atom-index map;
- has `StructureOrigin.RELAXED`;
- directly references the Calculation input snapshot through `parent_snapshot_id`;
- preserves the input snapshot periodicity tuple.

A candidate snapshot may be reconstructed even when the scientific result is unconverged. That makes
failed or unconverged geometries inspectable without declaring them scientifically accepted.

### 5. Promotion requires exact convergence evidence

Promotion consumes the exact `VaspConvergenceEvidence`, not scheduler state. The evidence must bind
the same Calculation and the same result `intake_hash` as the reconstruction. The existing pure
recipe-aware classifier is rerun and promotion is allowed only when the overall scientific verdict is
`CONVERGED`.

Execution success, scheduler completion, or mere CONTCAR presence cannot authorize promotion.

### 6. Promotion is stale-safe

Before promotion, `StructureVariant.current_structure_snapshot_id` must still equal the Calculation
input snapshot. If the variant has advanced since the Calculation started, promotion fails closed.
An old result therefore cannot overwrite a newer structure revision.

### 7. Promotion is pure and explicit

Promotion returns a new immutable `StructureVariant` whose current pointer references the
reconstructed candidate. The original variant and input snapshot remain unchanged. Storage mutation,
provenance persistence, and downstream continuation remain caller-controlled and are not performed by
Block 6.

### 8. Project schema remains unchanged

Block 6 adds no permanent top-level Entity and no schema migration. `SCHEMA_VERSION` remains 2.

## Parsing policy

The Block 6 parser accepts the VASP5-style POSCAR/CONTCAR representation produced by the managed
input/execution path, including Direct and Cartesian coordinates and optional Selective Dynamics
headers. Negative universal scaling factors, VASP4 symbol-less structures, malformed atom counts,
singular lattices, path escapes, and content drift fail closed rather than being guessed.

## Non-scope

Block 6 does not add:

- automatic continuation from CONTCAR;
- automatic restart or INCAR correction;
- promotion of unconverged or indeterminate results;
- geometric nearest-neighbour atom matching;
- lifecycle status reconciliation;
- RESULT_PARSE/CONVERGENCE Analysis persistence;
- legacy existing-folder importer unification;
- frequency interpretation or thermochemistry;
- DOS/PDOS/Bader/charge-difference/COHP analysis;
- workflow orchestration;
- GUI, tag, GitHub Release, or PyPI publication.

## Consequences

v0.5 can now represent a VASP relaxation result as an inspectable immutable structure candidate and,
only after an explicit convergence-aware gate, advance a StructureVariant to that candidate. This
preserves the project rule that result parsing, structure reconstruction, promotion, and future
continuation are separate scientific decisions.
