# ADR-012: v0.3 K-Point Policy Semantics

- Status: Accepted
- Date: 2026-09-03

## Context

ADR-011 reserves k-point selection for a later v0.3 preparation block and keeps the persisted
`KPointPolicy` schema intentionally small. Block 4 is the first implementation that must turn
that latent policy into deterministic VASP input. The scientific meaning of reciprocal density,
mesh centering, slab vacuum-axis handling, and the KSPACING/KPOINTS boundary therefore must be
fixed explicitly rather than inferred independently by future generators.

## Decision

### No frozen-schema change

`KPointPolicy`, `ProtocolDefinition`, `ProjectNumericalLock`, and schema version 1 remain
unchanged. Block 4 adds preparation-layer contracts under `ecatvasp.vasp`.

### Four policy kinds remain distinct

- `EXPLICIT_MESH`: caller supplies the exact integer mesh; ECatVASP writes `KPOINTS`.
- `RECIPROCAL_DENSITY`: `value` means k-point grid density per inverse-Angstrom cubed of the
  reciprocal cell (the established `kppvol` / reciprocal-volume-density meaning). ECatVASP
  derives a deterministic mesh and writes `KPOINTS`.
- `KSPACING`: `value` is VASP `KSPACING` in inverse Angstrom. ECatVASP does not write a normal
  `KPOINTS` file and instead hands `KSPACING` plus `KGAMMA` to the later INCAR block.
- `GAMMA_ONLY`: canonical `1 x 1 x 1` Gamma-centered `KPOINTS`.

The reciprocal-density mesh follows the established reciprocal-volume-density algorithm used by
mature VASP tooling: the target grid density is scaled by reciprocal-cell volume and divisions
are distributed according to lattice-vector lengths. ECatVASP records the resulting mesh so the
resolved numerical input is inspectable. The algorithm is a deterministic generator contract,
not a universal recommendation for the density value itself.

### Centering is explicit scientific protocol identity

`EXPLICIT_MESH`, `RECIPROCAL_DENSITY`, and `KSPACING` require an explicit preparation-layer
centering choice: `gamma` or `monkhorst_pack`. `GAMMA_ONLY` is intrinsically Gamma-centered.

The frozen `KPointPolicy` schema has no centering field, so the effective centering is copied into
`ProtocolDefinition.extra_parameters` under the namespaced scalar
`ECATVASP_KPOINT_CENTERING`. This makes Gamma versus Monkhorst-Pack change the Protocol hash
without adding a persisted domain field.

Geometrically hexagonal cells reject Monkhorst-Pack automatic meshes and require Gamma centering,
consistent with VASP symmetry guidance. ECatVASP does not infer "graphene" from element names.

### Physical-system rules

- `MOLECULE_0D` uses only canonical `GAMMA_ONLY` / `1 x 1 x 1`.
- `SLAB_2D` requires the declared vacuum-axis mesh component to be exactly `1`.
- For slab `RECIPROCAL_DENSITY`, the generated vacuum-axis component is fixed to `1` by contract.
- For slab `EXPLICIT_MESH`, a caller-supplied vacuum-axis component other than `1` fails closed.
- For slab `KSPACING`, ECatVASP predicts VASP's mesh from the reciprocal lattice and rejects the
  policy when VASP would sample the declared vacuum axis with more than one division. KSPACING
  cannot override one axis independently, so silently clamping it would be scientifically false.
- `PERIODIC_3D` has no forced unit axis.

### KSPACING and KPOINTS are mutually exclusive

A KSPACING plan materializes no `KPOINTS` file. A KPOINTS-backed plan requires one. The
preparation layer exposes a fail-closed presence validator so later input materialization and
preflight can reject `KSPACING_WITH_KPOINTS_CONFLICT` instead of relying on VASP's precedence.

### No universal k-point density

Block 4 does not define a product-wide lateral mesh, reciprocal-density value, or KSPACING value.
Project-selected production k-point policies remain convergence-sensitive and are carried by
`ProjectNumericalLock` into `ProtocolDefinition`. The existing `KPointConvergencePoint` recipe
remains the pre-lock path for convergence work; later materialization/preflight blocks bind the
validation artifact into the full execution manifest.

## Consequences

- K-point generation is deterministic and inspectable without changing frozen scientific schema.
- Gamma/Monkhorst-Pack differences participate in Protocol identity.
- Molecule and slab inputs cannot silently acquire physically inappropriate sampling directions.
- KSPACING cannot be accidentally neutralized by a simultaneously materialized `KPOINTS` file.
- Reciprocal-density values have a fixed unit/meaning, while the actual density remains a
  convergence decision rather than an ECatVASP default.
