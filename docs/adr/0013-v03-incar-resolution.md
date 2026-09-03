# ADR-013: v0.3 Scientific INCAR Resolution

- Status: Accepted
- Date: 2026-09-03

## Context

Block 5 turns the frozen Method / Protocol / Recipe identity model into deterministic VASP
`INCAR` text. This is a scientific compiler boundary, not a generic template merger. A generated
`INCAR` must not acquire physical assumptions from element names, catalyst labels, prior files,
or execution settings that are absent from the fingerprinted scientific inputs.

The preceding v0.3 blocks already establish deterministic POSCAR ordering, POTCAR metadata and
ENCUT validation, and k-point preparation. Block 5 must consume those contracts without changing
schema version 1 and without pulling SSH, scheduler, or execution-only tuning into the scientific
fingerprint.

## Decision

### Compiler inputs and deterministic output

The Block 5 compiler consumes the exact `StructureSnapshot`, `MethodDefinition`,
`ProtocolDefinition`, `RecipeIdentity`, `VaspSystemContext`, `PreparedPoscar`, `PreparedKPoints`,
`PotcarSpec`, and, for production recipes, a `ProjectNumericalLock`.

The compiler emits an immutable `PreparedIncar` containing sorted effective parameters, each
parameter's scientific source layer, deterministic text, content SHA-256, and a preparation
identity hash. A parameter may have only one scientific source. Conflicting or unsupported
pass-through settings fail closed.

Execution-only parameters such as `NCORE`, `KPAR`, `NPAR`, MPI ranks, scheduler directives, and
remote paths are outside this compiler and remain outside the scientific fingerprint.

### ECAT_STANDARD baseline

`ECATVASP_ECAT_STANDARD` keeps the already frozen relaxation force criterion
`EDIFFG = -0.02 eV/Å` and additionally defines the following reproducibility baseline for Block 5:

- `PREC = Accurate`
- `LREAL = .FALSE.`
- `LASPH = .TRUE.`
- `ALGO = Normal`

`LASPH` and `ALGO` must be explicitly fingerprinted in `ProtocolDefinition.extra_parameters`;
the compiler does not silently insert un-fingerprinted scientific settings. For slab dipole
corrections, the declared vacuum axis is likewise fingerprinted through
`ECATVASP_DIPOLE_AXIS`.

Block 5 does **not** create universal defaults for ENCUT, k-point density/KSPACING, EDIFF,
ISMEAR, or SIGMA. The effective values are taken from the validated Protocol / Project lock.

### Core recipe defaults

Block 5 materializes only the core recipe family required before Block 7:

- `SlabRelax`, `AdsorbateRelax`, `GasRelax`: `IBRION=2`, `ISIF=2`, `NSW=200`,
  `LCHARG=.FALSE.`, `LWAVE=.FALSE.`
- `GroundStateStatic`: `IBRION=-1`, `NSW=0`, `LCHARG=.TRUE.`, `LWAVE=.FALSE.`
- `ENCUTConvergencePoint`, `KPointConvergencePoint`: `IBRION=-1`, `NSW=0`,
  `LCHARG=.FALSE.`, `LWAVE=.FALSE.`

These defaults are part of canonical recipe version 1. Recipe-owned overrides are represented by
`RecipeIdentity.parameters`, so an override changes recipe identity. Frequency-specific settings
(`IBRION=5/6`, `NFREE`, `POTIM`) remain Block 8. DOS/PDOS, charge-density, and LOBSTER prerequisite
flags remain Block 9.

### XC, dispersion, and DFT+U

Block 5 supports deterministic mappings for PBE, RPBE, PBEsol, SCAN, and r2SCAN. Unsupported XC
labels fail closed rather than being guessed.

Dispersion is explicit. `MethodDefinition.dispersion_model` must be `NONE`, `IVDW=11`, or
`IVDW=12` in this block. In particular, graphene/carbon-containing structures do not implicitly
select a vdW correction.

The current structured `DftUSetting` is compiled as VASP Dudarev `LDAUTYPE=2`. `LDAUL`, `LDAUU`,
and `LDAUJ` arrays follow `PreparedPoscar.species_order`; species without U use `-1/0/0`.
Alternative DFT+U formalisms require an explicit future contract rather than a silent override.

### Spin and atom identity

Spin initialization is bound to permanent `atom_uid`, not element defaults and not POSCAR-local
indices. `UidMagmom` is converted to the local VASP order only at INCAR preparation time.
`ProtocolDefinition.initialization_parameters` must contain the exact
`ECATVASP_MAGMOM_UID_HASH` so the initial magnetic state participates in Protocol identity.

- `UNPOLARIZED` emits `ISPIN=1` and forbids MAGMOM initialization.
- `COLLINEAR` emits `ISPIN=2` and requires exactly one MAGMOM component for every atom UID.
- `NONCOLLINEAR` emits `LNONCOLLINEAR=.TRUE.` and requires three MAGMOM components per atom.
- SOC adds `LSORBIT=.TRUE.` only to a noncollinear method.

The compiler never guesses moments from element names. Missing, extra, duplicated, or
POSCAR-order-only magnetic initialization fails closed.

### Charge and POTCAR ZVAL

`MethodDefinition.charge_e` uses the convention that positive values mean positive cell charge,
i.e. electrons removed from the neutral valence count. For non-zero charge:

`NELECT = Σ(species_count × POTCAR_ZVAL) - charge_e`

The neutral count is derived from the exact ordered `PotcarSpec`; `charge_e` is never written
directly as `NELECT`.

### Dipole correction and electric field

Dipole behavior remains a Protocol decision resolved with explicit physical context:

- `OFF`: emits `LDIPOL=.FALSE.` and rejects explicit dipole tags.
- `AUTO` slab: emits `LDIPOL=.TRUE.` and `IDIPOL` along the declared vacuum axis; `DIPOL` is
  omitted so VASP performs its documented automatic center determination.
- `AUTO` molecule: emits `LDIPOL=.TRUE.` and `IDIPOL=4`.
- periodic 3D systems must use `OFF` in Block 5.
- `EXPLICIT` requires fingerprinted `IDIPOL` and three-coordinate `DIPOL`; slab `IDIPOL` must
  match the declared vacuum axis.

Whenever `LDIPOL` is active, the correction direction must be geometrically orthogonal to the
other lattice vectors; molecule `IDIPOL=4` requires an orthogonal cell. For charged systems,
VASP's potential correction is currently implemented only for cubic supercells, so a non-cubic
charged calculation with `LDIPOL=.TRUE.` fails closed during Block 5 preparation instead of being
allowed to reach a VASP runtime stop. Unsupported cells are never silently accepted.

`electric_field_ev_per_angstrom` is emitted as VASP `EFIELD` without changing VASP's sign
convention. In Block 5 it is supported only for slab contexts with a resolved `IDIPOL=1..3`.

### Solvation

No solvation is emitted for `None`/`NONE`. The explicit `VASPsol` method identity emits
`LSOL=.TRUE.`. Other solvation backends or parameters require a future explicit contract.

### Pass-through policy

Block 5 does not silently forward arbitrary Method extras. Protocol extras are restricted to the
Block 5 scientific contract plus internal namespaced identity metadata; core Recipe overrides
are restricted to core recipe-owned tags. This is deliberately stricter than a free-form INCAR
editor. Broader expert overrides can be added later with explicit layer ownership and validation.

## Consequences

- INCAR text becomes deterministic and traceable to Method / Protocol / Recipe / context rather
  than a loose configuration dictionary.
- Magnetic initialization, dipole axis, k-point centering, vdW policy, and project numerical
  locks cannot silently diverge from scientific identity.
- Charged calculations use POTCAR metadata correctly rather than confusing charge with NELECT.
- Charged `LDIPOL` calculations are rejected before execution when the cell violates VASP's
  current cubic-supercell restriction.
- Frequency and analysis-prerequisite semantics remain isolated to their planned Blocks 8 and 9.
- Execution performance tuning remains cleanly separated for the later execution handoff.
