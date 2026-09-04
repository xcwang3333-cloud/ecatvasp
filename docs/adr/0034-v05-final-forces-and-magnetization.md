# ADR-034: v0.5 Final Forces and Magnetization Boundary

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-030 defines a parser-neutral VASP result document, ADR-032 adds explicit energy and metadata
facts, and ADR-033 classifies convergence without mutating lifecycle state. Block 5 must add final
force and spin-magnetization observables while preserving permanent atom identity and avoiding two
scientific ambiguities: VASP output ordinals are serialization-local rather than stable atom
identities, and site-projected magnetization is not the same quantity as cell-integrated
magnetization.

The v0.3 input pipeline already materializes an immutable `atom-index-map.json` beside the exact
POSCAR. It binds POSCAR order to permanent `atom_uid` values and is staged through the
`ExecutionPlan`. Block 5 therefore does not infer atom correspondence geometrically.

## Decision

### 1. Forces and magnetization enrich the normalized result document

Block 5 introduces `parse_vasp_forces_magnetization()`. It consumes the exact Calculation,
MethodFingerprint, ExecutionPlan, result Artifact intake, and existing `VaspResultDocument`, then
returns a new immutable result document containing optional force and magnetization observables.

The adapter does not classify convergence, mutate Calculation or ExecutionAttempt state, persist an
Analysis, or promote a structure.

### 2. Atom identity comes only from the exact staged atom-index map

The parser requires exactly one `atom_index_map` and one `poscar` staging input from the same
ExecutionPlan referenced by the result intake. It verifies:

- Calculation, MethodFingerprint, plan, intake, recipe, and CalculationType identity;
- result sources exactly equal the admitted intake sources;
- plan hash and input-manifest hash match the intake;
- staged POSCAR and atom-index map remain project-root confined and content-addressed;
- atom-index-map format/version are supported;
- atom-index-map input snapshot matches `Calculation.input_structure_snapshot_id`;
- the map binds the exact staged POSCAR SHA-256;
- POSCAR indices and VASP ordinals are contiguous;
- permanent `atom_uid` values are unique;
- species counts equal the mapped atom count.

No nearest-neighbor, element-only, or coordinate-tolerance atom matching is permitted.

### 3. Final forces are Cartesian per-atom vectors

The parser reads VASP `POSITION ... TOTAL-FORCE (eV/Angst)` blocks from the exact OUTCAR and uses
only the final force block. Each vector is mapped from VASP ordinal to permanent `atom_uid` and
stored in `VaspForceDataset` as eV/angstrom Cartesian components. The dataset derives the Euclidean
maximum force magnitude.

If a later/final force block is incomplete, Block 5 fails closed instead of silently reusing an
earlier complete ionic step. Absence of any force block is represented as `None` rather than an
invented zero force.

### 4. Collinear and noncollinear magnetization have explicit shapes

`MethodFingerprint.method.spin_treatment` determines the expected spin shape:

- `UNPOLARIZED`: no spin-magnetization dataset is produced; contradictory nonzero/site evidence is
  rejected;
- `COLLINEAR`: site-projected moments are scalar values and only `magnetization (x)` tables are
  accepted;
- `NONCOLLINEAR`: site-projected moments are three-component vectors. If projected site tables are
  present, the final x/y/z tables must belong to the same final output group.

Noncollinear vectors are described as components in the VASP spinor basis. Block 5 does not infer an
external Cartesian spin basis that is not encoded in the current Method contract.

### 5. Cell-integrated and site-projected moments remain separate

The OUTCAR `number of electron ... magnetization ...` value is stored as the cell-integrated spin
moment. The `tot` row of `magnetization (x/y/z)` tables is stored separately as the projected total,
and per-ion `tot` values are stored as site-projected moments.

These quantities are intentionally not required to be numerically equal. Projection inside atomic
spheres and cell integration have different semantics.

### 6. Orbital magnetic moments are not hidden inside spin magnetization

Block 5 parses spin magnetization only. Orbital moments printed by VASP under separate orbital
magnetization outputs are outside this contract and must not be added to the spin values implicitly.

### 7. Source integrity is revalidated at observable-parse time

OUTCAR, atom-index-map, and staged POSCAR bytes are rechecked against their recorded byte sizes and
SHA-256 digests before their scientific content is used. This closes the result-parse to observable-
parse TOCTOU gap without changing execution provenance.

### 8. Result document format advances without a project schema migration

`VASP_RESULT_DOCUMENT_VERSION` advances from 1 to 2 because the normalized value contract gains
optional force and magnetization fields. Existing energy/metadata semantics remain unchanged.

The Project storage schema remains version 2. Block 5 adds no new permanent top-level Entity and no
runtime dependency.

## Non-scope

Block 5 does not add:

- stress-tensor parsing;
- orbital magnetization;
- eigenvalues, DOS, PDOS, Bader, charge difference, or COHP analysis;
- convergence reclassification or Calculation scientific-status reconciliation;
- RESULT_PARSE/CONVERGENCE Analysis persistence or PARSED_RESULT persistence;
- CONTCAR reconstruction, atom-UID promotion, or automatic continuation;
- frequency-mode interpretation or thermochemistry;
- scientific workflow orchestration;
- automatic VASP correction/restart logic;
- GUI work, tag, GitHub Release, or PyPI publication.

## Consequences

v0.5 now has UID-addressed final force vectors and spin-aware magnetization observables without
weakening the execution/result/provenance boundary. Block 6 can reconstruct and explicitly promote
CONTCAR geometry using the same immutable atom-order identity, while Block 8 can later persist and
unify the complete result pipeline without reinterpreting these scientific quantities.
