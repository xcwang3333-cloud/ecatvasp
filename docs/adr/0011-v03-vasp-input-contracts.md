# ADR-011: v0.3 VASP Input Preparation Contracts

- Status: Accepted
- Date: 2026-09-03

## Context

v0.3 adds deterministic VASP input preparation on top of the frozen schema-version-1
scientific model. The pipeline must distinguish physical Method choices, numerical Protocol
choices, task Recipe identity, and execution-only tuning while refusing to guess
system-dependent settings. It must also preserve `atom_uid` as the scientific atom identity,
keep licensed POTCAR contents outside ECatVASP storage/distribution, and stop before remote
scheduler implementation.

The existing domain already provides `Calculation`, `ExecutionAttempt`, `Artifact`,
`MethodFingerprint`, provenance/freshness semantics, and the Method / Protocol / Recipe /
Execution boundary from ADR-005. Block 1 therefore must not introduce a parallel calculation
schema or silently mutate the frozen domain.

## Decision

### Frozen-domain boundary

Block 1 makes no schema-version change. `Project`, `StructureSnapshot`, `Calculation`,
`MethodDefinition`, `ProtocolDefinition`, `RecipeIdentity`, and `MethodFingerprint` retain their
current persisted forms. New v0.3 contracts live in `ecatvasp.vasp` as preparation-layer value
objects. If a later block proves that a persisted scientific identity cannot be represented by
the frozen model, that change requires a new ADR before implementation.

### Explicit VASP system context

Input generation receives an explicit `VaspSystemContext` with one of three physical contexts:

- `SLAB_2D`
- `MOLECULE_0D`
- `PERIODIC_3D`

A slab context must declare its vacuum lattice axis. ECatVASP does not infer physical context
from element names, atom count, or cell dimensions. The context is used later for k-point,
dipole, slab, and validation policies; it is not a replacement for `StructureSnapshot`.

### Project numerical lock

`ProjectNumericalLock` is a method-aware preparation policy keyed by Project, physical system
kind, and `core_method_hash`. It records the validated effective ENCUT, its validation digest,
and the project-selected k-point policy. The lock itself is not a new frozen scientific entity.
Before a production Calculation becomes input-ready, its effective ENCUT and k-point policy
must be copied into `ProtocolDefinition`, so the final `MethodFingerprint` remains the
authoritative scientific identity.

ENCUT has no universal product default. The intended v0.3 flow remains:

`POTCAR ENMAX suggestion -> convergence validation -> ProjectNumericalLock -> ProtocolDefinition -> MethodFingerprint`.

Convergence-point recipes are explicitly allowed before the project lock exists; production
recipes fail closed without a lock.

### ECAT standard

The canonical electrocatalysis standard identifier is `ECATVASP_ECAT_STANDARD`. Its frozen
geometry-optimization force criterion remains `EDIFFG = -0.02 eV/Å`. Block 1 deliberately does
not turn system-dependent settings such as ENCUT, k-point density, spin initialization, vdW,
or dipole correction into universal defaults. Their effective resolution belongs to later v0.3
blocks and must be fingerprinted when scientifically relevant.

### Canonical v0.3 recipe registry

The preparation layer defines stable `ECatVASP.VASP.*` recipe identities mapped to the existing
`CalculationType` taxonomy. Selected-atom and full frequency remain the same scientific
Calculation type and are distinguished by Recipe identity.

Canonical recipes are:

- `SlabRelax` -> `RELAX`
- `AdsorbateRelax` -> `RELAX`
- `GasRelax` -> `GAS_RELAX`
- `GroundStateStatic` -> `STATIC`
- `SelectedAtomFrequency` -> `FREQUENCY`
- `FullFrequency` -> `FREQUENCY`
- `GasFrequency` -> `GAS_FREQUENCY`
- `DOSPrerequisite` -> `DOS_STATIC`
- `ChargeDensityStatic` -> `CHARGE_STATIC`
- `LobsterPrerequisite` -> `LOBSTER_PREREQUISITE`
- `ENCUTConvergencePoint` -> `STATIC`
- `KPointConvergencePoint` -> `STATIC`

The registry also declares allowed physical system contexts and whether a validated project
numerical lock is required. Unknown recipes, CalculationType mismatches, incompatible physical
contexts, and missing/wrong-project locks fail closed before input generation.

Historical ad-hoc recipe strings already stored in old development fixtures are not silently
rewritten. v0.3 generators use the canonical registry going forward.

### v0.4 boundary

Block 1 does not implement SSH, Slurm/PBS/LSF submission, scheduler polling, remote staging,
retry handlers, or `RemoteJob` creation. v0.3 will eventually terminate at an immutable input
manifest / `ExecutionPlan` handoff consistent with ADR-007. Scheduler adapters remain v0.4
work.

## Consequences

- v0.3 can add a strict input compiler without duplicating the frozen scientific model.
- Effective production settings remain reproducible because they must enter the existing
  `MethodFingerprint` rather than living only in UI/project preferences.
- Selected/full frequency semantics are expressible without expanding `CalculationType`.
- ENCUT convergence can run before a project lock while production recipes fail closed.
- K-point, POTCAR, input materialization, frequency, prerequisite, and execution-handoff blocks
  now have explicit contracts to build against.
