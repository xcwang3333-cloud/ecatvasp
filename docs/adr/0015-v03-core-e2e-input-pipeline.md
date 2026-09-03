# ADR-015: v0.3 Core VASP End-to-End Input Pipeline

- Status: Accepted
- Date: 2026-09-03

## Context

Blocks 2–6 established independent deterministic contracts for POSCAR serialization, licensed POTCAR metadata resolution, k-point preparation, Method/Protocol/Recipe-to-INCAR compilation, immutable Artifact materialization, and provenance/freshness. Block 7 needs a production entry point for the ordinary relaxation and ground-state workflows without duplicating those scientific rules or crossing into scheduler execution.

The target workflows are:

- `ECatVASP.VASP.SlabRelax` on `SLAB_2D`;
- `ECatVASP.VASP.AdsorbateRelax` on `SLAB_2D`;
- `ECatVASP.VASP.GasRelax` on `MOLECULE_0D`;
- `ECatVASP.VASP.GroundStateStatic` on any allowed system context.

Gas-phase static calculations therefore use `GroundStateStatic + MOLECULE_0D`. No separate GasStatic CalculationType or recipe is introduced.

## Decision

Block 7 adds a preparation-layer orchestration API only. It does not change the frozen Domain or storage schema.

The pipeline order is:

1. validate Calculation, StructureSnapshot, MethodFingerprint, recipe, SystemContext, and ProjectNumericalLock identity;
2. recover the unique fingerprinted `ECATVASP_KPOINT_CENTERING` value from the Protocol rather than guessing centering;
3. deterministically prepare POSCAR, including optional UID-addressed Selective Dynamics;
4. prepare the exact k-point plan from the fingerprinted Protocol and declared SystemContext;
5. validate the ProjectNumericalLock against k-point convergence evidence, with the existing molecule Gamma-only exception;
6. resolve the user-local licensed POTCAR library in POSCAR species order, retaining only local paths and redistribution-safe metadata;
7. validate the ProjectNumericalLock against exact POTCAR metadata and ENCUT convergence evidence;
8. compile INCAR from the exact MethodFingerprint Method/Protocol/Recipe, UID-addressed MAGMOM when required, and the already prepared inputs;
9. call the Block 6 public materializer, including its exact-fingerprint INCAR recompilation guard;
10. return one immutable preparation-layer result containing the prepared inputs, verified POTCAR path set, and materialized Artifact/manifest result.

## Numerical-lock evidence

A production `ProjectNumericalLock` is not treated as self-authenticating by the Block 7 orchestration layer.

- `EncCutValidationEvidence` is required and must match the exact core-method hash, POTCAR metadata hash, selected ENCUT, and lock validation hash.
- Solid-state calculations require matching `KPointValidationEvidence` for the exact prepared k-point plan.
- `MOLECULE_0D + GAMMA_ONLY` may omit separate k-point convergence evidence only under the pre-existing Block 4 rule that the project lock also carries no k-point validation hash.

This keeps the production pipeline downstream of numerical convergence rather than allowing a manually fabricated lock to bypass convergence provenance.

## POTCAR boundary

The pipeline may read user-installed licensed POTCAR files locally to validate hash, TITEL, ZVAL, and ENMAX and to return ordered local paths. It never copies, concatenates, commits, packages, persists, or materializes the licensed POTCAR body into the ECatVASP project.

`POTCAR.spec` remains the only project-side POTCAR representation in v0.3.

## Identity and determinism

The pipeline does not infer scientific choices from geometry or defaults outside existing contracts.

- physical dimensionality comes from `VaspSystemContext`;
- k-point centering comes from the fingerprinted Protocol;
- POTCAR identities come from `MethodDefinition` and are verified against local files;
- ENCUT and k-point production values come from a validated ProjectNumericalLock and matching evidence;
- INCAR comes from the exact MethodFingerprint;
- atom identity remains permanent `atom_uid`, with POSCAR index local to serialization;
- final filesystem identity is the deterministic Block 6 input manifest SHA-256.

Changing any of these inputs requires a different prepared/materialized input set and cannot silently overwrite an existing conflicting Calculation input directory.

## Boundaries

Block 7 does not implement:

- selected/full/gas frequency semantics;
- DOS/PDOS prerequisites;
- charge-density triplets or charge-difference analysis;
- LOBSTER prerequisite generation;
- real POTCAR staging;
- ExecutionAttempt creation;
- SSH, scheduler scripts, RemoteJob, retry, resource tuning, or scheduler DAG logic.

Those remain assigned to later v0.3 blocks or v0.4.

## Consequences

The ordinary slab/adsorbate/gas relaxation and ground-state static workflows now have one auditable path from scientific Calculation identity through deterministic VASP inputs to immutable Artifacts and a manifest. Later execution code can consume the manifest without re-resolving scientific settings, while licensed POTCAR staging and scheduler concerns remain separate.
