# ADR-036: v0.5 Frequency Scientific Results Boundary

- Status: Accepted
- Date: 2026-09-04

## Context

v0.3 established finite-difference frequency recipes with explicit `IBRION=5/6`, `NFREE=2`,
`POTIM`, tight electronic convergence, and permanent-UID selected-atom semantics. v0.5 Blocks 1–6
then established parser-neutral result documents, exact result Artifact intake, energy/metadata parsing,
recipe-aware convergence, UID-addressed forces/magnetization, and explicit relaxed-structure promotion.

Block 7 must expose VASP vibrational modes as scientific results without prematurely entering the
thermochemistry layer. In particular, VASP distinguishes real modes (`f=`) from imaginary modes
(`f/i=`), selected-atom frequency calculations have fewer active finite-difference degrees of
freedom than the full atom count, and `NWRITE=3` is required only for the additional
`Eigenvectors after division by SQRT(mass)` representation. The existing ECatVASP frequency recipe
does not require `NWRITE=3`, so that optional representation cannot be made a mandatory result
source without changing the already-frozen input protocol.

## Decision

### 1. Frequency parsing enriches the normalized VASP result document

Block 7 introduces `parse_vasp_frequency_results()`. It consumes the exact Calculation,
MethodFingerprint, ExecutionPlan, result Artifact intake, input StructureSnapshot, and existing
`VaspResultDocument`, then returns a new immutable result document with an optional
`VaspFrequencyDataset`.

The adapter does not mutate Calculation or ExecutionAttempt state, persist an Analysis, classify
thermodynamic stability, or calculate any thermal correction.

### 2. The standard dynamical-matrix OUTCAR block is canonical

The canonical source is the standard OUTCAR block headed:

`Eigenvectors and eigenvalues of the dynamical matrix`

This block is available independently of the optional `NWRITE=3` mass-divided representation.
Block 7 therefore parses exactly one canonical standard block and ignores a later block explicitly
introduced by `Eigenvectors after division by SQRT(mass)`.

If no canonical block exists, or multiple canonical standard blocks make the source ambiguous,
parsing fails closed. Block 7 does not silently fall back to another representation.

### 3. Real and imaginary modes have explicit semantics

Each mode records:

- one-based VASP mode index;
- `VaspFrequencyModeKind.REAL` for `f=` or `IMAGINARY` for `f/i=`;
- the positive frequency magnitude reported by VASP in THz;
- the VASP `2PiTHz` value;
- the reported wavenumber in cm^-1;
- the reported mode energy in meV;
- the standard dynamical-matrix eigenvector components for every atom in exact VASP order.

Imaginary modes are **not** represented by inventing a negative frequency. The `f/i=` label is the
scientific observation; VASP's printed frequency magnitude is retained as printed.

Block 7 also deliberately does not add an ambiguous generic `eigenvalue` scalar. The OUTCAR mode
line reports several frequency representations, while the dynamical-matrix eigenvalue is related to
squared angular frequency. A later consumer must not guess which quantity an unlabeled
`eigenvalue` field means.

### 4. Standard eigenvectors are not silently reinterpreted as physical displacements

`VaspFrequencyEigenvector.components` stores the components from the canonical standard
dynamical-matrix block. Block 7 does not relabel these as the optional SQRT(mass)-divided atomic
displacements.

If a future visualization or reaction-coordinate feature requires the mass-divided representation,
it must introduce an explicit contract and source requirement rather than changing these semantics
in place.

### 5. Mode cardinality is recipe-aware and fail-closed

The immutable atom-index map remains authoritative for VASP order and Selective Dynamics flags.

- `SelectedAtomFrequency`: only whole-atom `T T T` / `F F F` selection is accepted. The selected
  UID set must match the exact `FrequencySelection` digest in MethodFingerprint, and the result must
  contain exactly `3 * N_selected` modes.
- `FullFrequency`: Selective Dynamics must be absent and the result must contain exactly
  `3 * N_atoms` modes.
- `GasFrequency`: Selective Dynamics must be absent and the result must contain exactly
  `3 * N_atoms` modes.

Each mode eigenvector table must contain every atom in exact VASP/POSCAR order, even when only a
subset of atoms contributes active finite-difference degrees of freedom.

### 6. Permanent atom identity is inherited only from the exact staged atom map

Block 7 revalidates the staged `atom-index-map.json` and POSCAR bytes by size/SHA-256, then requires:

- supported atom-map format/version;
- `structure_snapshot_id` equal to the Calculation input snapshot;
- `structure_sha256 == scientific_hash(input_snapshot)`;
- `poscar_sha256` equal to the exact staged POSCAR SHA-256;
- complete contiguous POSCAR/VASP order;
- a permutation of source snapshot indices;
- exact immutable `atom_uid` and element identity.

No nearest-neighbour, coordinate-tolerance, or element-only identity inference is permitted.

### 7. Result source integrity is revalidated at frequency-parse time

The exact OUTCAR admitted by `VaspResultArtifactIntake` is reopened only inside `project_root` and
revalidated against its recorded size and SHA-256. This closes the intake-to-frequency-parse TOCTOU
gap.

The normalized result must carry the exact same source identities as the result intake.

### 8. Imaginary modes are observations, not a thermodynamic/stability verdict

Block 7 records the number and identities of imaginary modes, but does not decide whether a
structure is a minimum, transition state, numerical artifact, surface translation, or otherwise
acceptable for thermochemistry. Such interpretation requires system- and workflow-specific policy
outside this Block.

### 9. Result-document version advances without a Project schema migration

`VASP_RESULT_DOCUMENT_VERSION` advances from 2 to 3 because the parser-neutral result value
contract gains optional frequency data.

Project `SCHEMA_VERSION` remains 2. No new permanent top-level Entity or runtime dependency is
introduced.

## Non-scope

Block 7 does not add:

- ZPE calculation;
- vibrational entropy or enthalpy;
- heat capacities;
- translational/rotational molecular thermochemistry;
- low-frequency cutoffs, quasi-RRHO, or hindered-rotor corrections;
- free-energy corrections or CHE;
- automatic acceptance/rejection based on imaginary modes;
- phonon dispersion, force-constant export, or q-point workflows;
- IR/Raman activities;
- automatic continuation/restart/correction;
- Calculation/ExecutionAttempt lifecycle mutation;
- RESULT_PARSE/CONVERGENCE persistence or legacy importer unification;
- DOS/PDOS/Bader/charge-difference/COHP analysis;
- GUI work, tag, GitHub Release, or PyPI publication.

## Consequences

v0.5 now has a provenance-bound, UID-addressed representation of finite-difference VASP vibrational
modes that can be consumed later by thermochemistry without conflating raw mode observations with
thermal corrections or stability policy. Block 8 can persist/unify this result path, while the later
Thermochemistry & Electrocatalysis phase can add explicit ZPE/entropy/reference-energy semantics on
top of these frozen raw scientific facts.
