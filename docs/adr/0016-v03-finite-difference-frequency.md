# ADR-016: v0.3 Finite-Difference Frequency Contract

- Status: Accepted
- Date: 2026-09-03

## Context

v0.3 Block 8 adds production input preparation for the three frequency recipes already frozen in the scientific recipe registry:

- `ECatVASP.VASP.SelectedAtomFrequency`
- `ECatVASP.VASP.FullFrequency`
- `ECatVASP.VASP.GasFrequency`

The implementation must preserve permanent `atom_uid` identity, exact Method/Protocol/Recipe fingerprints, the Project numerical-lock gate, and the licensed-POTCAR boundary established by Blocks 1–7.

VASP finite-difference semantics impose an important distinction. Selective Dynamics is supported for partial Hessian construction with `IBRION=5`; therefore selected-atom frequency calculations cannot be silently upgraded to the symmetry-reduced `IBRION=6` algorithm. Full-system calculations can use `IBRION=6`.

## Decision

### Selected-atom frequency

`SelectedAtomFrequency` uses:

- `IBRION=5`;
- `NSW=1`;
- `NFREE=2`;
- explicit positive `POTIM`;
- `LCHARG=.FALSE.`;
- `LWAVE=.FALSE.`.

The selected scientific atoms are expressed only as permanent `atom_uid` values. The selected UID set is order-independent and produces a deterministic SHA-256 digest stored in `MethodFingerprint.input_digests` under `frequency-selection-uids`.

At POSCAR rendering time only, selected UIDs become `T T T` and every other atom becomes `F F F`. Local POSCAR indices are never scientific identity.

### Full and gas frequency

`FullFrequency` and `GasFrequency` use:

- `IBRION=6`;
- `NSW=1`;
- `NFREE=2`;
- explicit positive `POTIM`;
- `LCHARG=.FALSE.`;
- `LWAVE=.FALSE.`.

They must not carry selected-atom Selective Dynamics or a `frequency-selection-uids` fingerprint digest.

`GasFrequency` remains `CalculationType.GAS_FREQUENCY` in `MOLECULE_0D`; `FullFrequency` remains the solid-system `CalculationType.FREQUENCY` recipe already defined by ADR-011.

### Recipe fingerprinting

Block 8 requires `NFREE` and `POTIM` to be explicit `RecipeIdentity.parameters`. `NFREE` is currently restricted to the central-difference stencil `2`; unsupported stencils fail closed instead of being inferred or passed through.

The displacement amplitude `POTIM` is therefore included in the recipe hash and cannot change without changing the scientific instance identity.

### Electronic convergence

`ECATVASP_ECAT_STANDARD` frequency calculations require `EDIFF <= 1e-8 eV`. Tighter values remain valid and are already fingerprinted by `ProtocolDefinition`.

### Compiler layering

Block 8 does not rewrite the established Block 5 Method/Protocol compiler. Frequency INCAR preparation reuses the audited core Method/Protocol/context compilation and replaces only the ionic recipe layer with the finite-difference controls above.

The public materialization guard dispatches frequency recipes to the frequency compiler and independently verifies the selected UID digest against the actual prepared POSCAR. Direct callers therefore cannot bypass selection identity by skipping the Block 8 E2E pipeline.

### E2E pipeline

The frequency pipeline retains the production numerical gates from Block 7:

1. validate Calculation / fingerprint / recipe / SystemContext / ProjectNumericalLock identity;
2. prepare frequency-specific POSCAR semantics;
3. prepare and validate the exact k-point plan;
4. validate solid k-point convergence evidence when required;
5. resolve local licensed POTCAR metadata/hash only;
6. validate ENCUT convergence evidence;
7. compile frequency INCAR;
8. run the exact-fingerprint materialization guard;
9. materialize immutable project-side input Artifacts and manifest.

No real POTCAR body is copied into the project.

## Consequences

- Selected vibrational atoms remain stable across species regrouping and POSCAR reserialization.
- Partial-Hessian calculations cannot silently use symmetry-reduced `IBRION=6`.
- Full and gas frequency calculations cannot accidentally inherit Selective Dynamics constraints.
- `NFREE`, `POTIM`, and selected UID identity are inspectable scientific provenance.
- The core Block 5/7 calculation paths remain unchanged.

## Out of scope

Block 8 does not implement:

- phonon result parsing or thermochemistry;
- imaginary-mode interpretation;
- DOS/PDOS prerequisites;
- charge-density triplets or charge-difference analysis;
- LOBSTER prerequisites;
- POTCAR staging;
- `ExecutionAttempt`, scheduler, SSH, `RemoteJob`, retry, or resource tuning.
