# ADR-058: v0.8 Thermochemistry Domain Contracts

- Status: Accepted
- Date: 2026-09-05

## Context

ADR-057 fixes the phase architecture: raw VASP modes remain parser facts, thermochemical policy is
an Analysis identity, component-resolved results are derived datasets, gas/reaction layers consume
those datasets, and existing SCIENTIFIC freshness remains authoritative. Block 1 provides the first
code boundary without yet evaluating a VASP frequency result.

## Decision

### 1. Conditions and standard state are explicit

`ThermochemicalConditions` requires positive temperature. Gas conditions also require explicit
actual pressure and one of `IDEAL_GAS_1_BAR` or `IDEAL_GAS_1_ATM`; fixed-cell surface conditions
reject gas pressure. One bar and one atmosphere are distinct constants and identities.

### 2. VASP electronic-energy semantics are selected by name

`ElectronicEnergyKind` maps to the existing explicit result fields:

- `SIGMA_ZERO -> energy_sigma0_ev`
- `WITHOUT_ENTROPY -> energy_without_entropy_ev`
- `TOTEN -> free_energy_toten_ev`

Block 1 does not choose a default or substitute a missing field. A later materializer must request
one exact semantic and fail if it is absent.

### 3. Vibrational acceptance policy is part of identity

`VibrationalModePolicy` requires a positive frequency cutoff plus explicit imaginary- and
low-frequency policies. Optional mode exclusions use the original one-based VASP mode index and a
typed reason. Policies that say `REJECT` cannot simultaneously carry an exclusion that would hide
the rejected condition.

Block 1 intentionally provides no absolute-value conversion for imaginary frequencies, no automatic
quasi-RRHO floor, and no heuristic mode classifier.

### 4. Gas molecular metadata and atomic masses are explicit

`GasMoleculeModel` records monatomic/linear/nonlinear geometry class, symmetry number, spin
multiplicity, and exact atom-UID-bound masses. `GasAtomicMass` preserves the mass used for each
source atom and may carry an isotopologue label. A later gas materializer must validate that the
mass assignment is a complete permutation of the exact source snapshot/frequency atom UIDs; it may
not silently substitute an ASE periodic-table default.

Gas thermochemistry identity requires this molecular metadata and an ideal-gas standard state.
Spin-degeneracy electronic entropy is legal only when explicit gas molecular metadata exists.
Changing any mass or isotopologue assignment changes the Analysis parameters hash.

### 5. Thermochemistry identity is directly hashable and correction-complete

`ThermochemistryIdentity.parameters_hash` uses the existing canonical serializer. Temperature,
pressure, standard state, electronic-energy semantic, electronic-entropy policy, vibrational policy,
mode exclusions, gas geometry/symmetry/spin/masses, and all declared additive corrections therefore
produce deterministic identity drift.

Corrections are typed and carry value, label, policy id, and policy version in the identity. A result
must carry exactly the same correction tuple; the final free energy cannot change under an unchanged
Analysis identity by silently replacing a correction value.

The hash is intended to populate the existing `Analysis.parameters_hash`; no new persisted identity
entity is created.

### 6. Results preserve components instead of only a final G

`ThermochemistryComponents` stores electronic energy, ZPE, vibrational/translational/rotational
thermal-energy terms, the ideal-gas pV term, vibrational/translational/rotational/electronic entropy
terms, and the visible additive corrections. `ThermochemistryResult` may derive Gibbs energy from
those components, but the component values remain first-class dataset content.

Later Blocks must additionally bind correction source/provenance through exact Analysis inputs and
SCIENTIFIC dependencies.

### 7. Concrete mode selection is result evidence and must match identity

`ThermochemistryModeSelection` records accepted raw VASP mode indices and explicit exclusions. A
mode cannot be both accepted and excluded. An identity with vibrational policy requires a concrete
selection in its result; a result without vibrational policy rejects mode-selection evidence. The
result exclusion set must equal the exclusions declared by the identity policy exactly.

### 8. Scope remains value-contract only

Block 1 does not parse files, evaluate harmonic formulas from frequencies, calculate moments of
inertia, build a gas registry, apply CHE, evaluate reactions, materialize Analysis/Artifact rows, or
integrate workflow readiness. Those belong to subsequent Blocks.

Project `SCHEMA_VERSION` remains 3 and no runtime dependency is added.

## Consequences

v0.8 now has a parameter-complete, fail-closed scientific contract on which frequency
materialization can be implemented without changing the frozen v0.5 parser boundary or inventing a
second persisted thermochemistry state machine.
