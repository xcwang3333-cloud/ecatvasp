# ADR-019: v0.3 ExecutionPlan Handoff

- Status: Accepted
- Date: 2026-09-03

## Context

v0.3 now produces deterministic, reconciled VASP inputs and immutable input manifests, but it
must terminate before scheduler implementation. ADR-007 requires the scientific DAG to hand a
portable `ExecutionPlan` to future execution adapters, while ADR-005 requires execution-only
tuning to remain outside `MethodFingerprint` scientific identity.

The handoff must pin exactly what may be staged, preserve the licensed-POTCAR boundary, state
what outputs are expected/retrieved, and express VASP runtime compatibility without creating a
scheduler job. It must also fail closed if immutable project files or locally resolved licensed
POTCAR datasets change between input materialization and execution handoff.

## Decision

### Preparation-layer value object

`ExecutionPlan` lives in `ecatvasp.vasp` as an immutable preparation/execution-handoff value
object. It is not a new frozen Domain entity and does not change storage schema version 1.

The plan pins:

- the exact `Calculation` and canonical Recipe identity;
- `VaspSystemContext`;
- the input-manifest Artifact id, manifest SHA-256, and preparation hash;
- each redistribution-safe staging Artifact id, project-relative source path, run-directory
  target path, byte size, and SHA-256;
- a license-safe ordered POTCAR resolution request containing family, element, symbol, and exact
  dataset SHA-256, but no POTCAR body or host-local POTCAR path;
- recipe-aware expected outputs, required/optional status, and `RetrievalPolicy`;
- semantic VASP runtime/version capability constraints;
- `ExecutionSettings` and its execution hash, explicitly outside `MethodFingerprint`.

### Handoff integrity gate

Creating an `ExecutionPlan` re-validates the materialized input manifest and Artifact metadata,
then re-reads every project-local staging file and requires exact size/SHA-256 agreement. Paths
must be normalized project-relative POSIX paths and may not escape `project_root`.

Locally licensed POTCAR files are also re-hashed at handoff. Their local filesystem paths are
used only while building the plan and are never retained in the portable plan.

### Execution-only settings

`NCORE`, `KPAR`, MPI rank/thread intent, the logical executable name, and other execution-only
settings may alter the `ExecutionPlan` hash but never alter `MethodFingerprint`.

v0.3 does not select scheduler resources. `nodes`, `cores`, `memory_mb`, `walltime_seconds`, and
`partition` therefore fail closed in the v0.3 plan builder and remain v0.4 adapter concerns.
The executable must be a portable command name rather than a host-specific filesystem path.

Future execution adapters may render attempt-specific execution overlays, but they may not
silently mutate the immutable scientific source inputs or their manifest. Such attempt-specific
changes belong to execution provenance.

### Output contract

All v0.3 VASP execution plans require `OUTCAR`; `OSZICAR` is optional but retained by default.
Recipe-specific required outputs include:

- relaxation recipes: `CONTCAR`;
- `DOSPrerequisite`: `DOSCAR`;
- `ChargeDensityStatic`: `CHGCAR`;
- `LobsterPrerequisite`: `WAVECAR`.

Frequency and ordinary static/convergence recipes use the common output contract unless a later
accepted ADR adds another required artifact.

### v0.4 boundary

Block 11 does not create `ExecutionAttempt` or `RemoteJob`, generate Slurm/PBS/LSF scripts,
choose queues/partitions, create remote directories, submit/poll/cancel jobs, perform retries, or
stage/retrieve files over SSH. Those responsibilities start in v0.4 execution adapters.

## Acceptance

The v0.3 final acceptance must traverse a real Model Studio electrocatalysis workflow:

`graphene -> vacancy/N environment -> opposite-side Pb2 -> ActiveSite -> multicenter *COOH ->
AdsorptionState/StateConformer/BindingEdge -> Calculation/MethodFingerprint -> deterministic
VASP inputs/manifest -> ExecutionPlan`.

Permanent adsorbate/site `atom_uid` identities must remain present in the generated POSCAR index
map; POSCAR serialization indices remain local generation-time indices only.

## Consequences

- v0.3 ends at a portable, integrity-checked execution handoff rather than a scheduler object.
- execution tuning can change without invalidating scientific Method identity.
- licensed POTCAR contents remain outside ECatVASP storage and plan serialization.
- future local/SSH/Slurm adapters can evolve independently while consuming one stable handoff.
- the final v0.3 acceptance joins Model Studio scientific identity to the complete VASP input
  pipeline without collapsing scientific and execution DAG boundaries.
