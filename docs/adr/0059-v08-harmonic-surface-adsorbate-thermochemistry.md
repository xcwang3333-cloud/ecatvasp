# ADR-059: v0.8 Harmonic Surface/Adsorbate Thermochemistry

- Status: Accepted
- Date: 2026-09-05

## Context

ADR-057 defines thermochemistry as a derived Analysis layer above durable VASP parser facts, and
ADR-058 defines parameter-complete thermochemistry identities and component-resolved results.
Block 2 connects those contracts to the existing v0.5 frequency result/provenance pipeline for
fixed-cell surface and adsorbate systems only.

The v0.5 parser already records explicit VASP electronic-energy semantics and a Γ-point
`VaspFrequencyDataset` with real/imaginary mode identity, one-based raw mode indices, energies,
wavenumbers, permanent atom UIDs, displaced atom UIDs, and exact atom-UID-bound eigenvectors.
Those parser facts remain immutable and policy-free.

## Decision

### 1. Block 2 consumes only converged `FREQUENCY` calculations

`materialize_harmonic_thermochemistry()` accepts `CalculationType.FREQUENCY` and requires
`CalculationScientificStatus.CONVERGED`.

`GAS_FREQUENCY` is deliberately rejected in this Block. Ideal-gas translation, rotation,
symmetry-number, spin-degeneracy, pressure, and molecular-mass semantics belong to Block 3.

### 2. Source identity is exact and fail-closed

Materialization requires one mutually consistent source chain:

- the `Calculation`;
- its exact `MethodFingerprint`;
- its exact input `StructureSnapshot`;
- a completed `RESULT_PARSE Analysis`;
- the locally available `PARSED_RESULT Artifact` produced by that Analysis;
- the in-memory `VaspResultDocument` whose canonical content matches the durable Artifact.

The parsed-result file must pass local-path confinement, byte-size, SHA-256, canonical format,
contract-version, Calculation-id, Analysis-id, and result-content checks. Missing or inconsistent
facts are errors; no filename or directory-name inference is permitted.

### 3. Electronic energy is selected by explicit semantic

The thermochemistry identity selects exactly one of:

- `energy_sigma0_ev`;
- `energy_without_entropy_ev`;
- `free_energy_toten_ev`.

Block 2 never substitutes another field when the selected semantic is absent.

### 4. Vibrational mode acceptance is explicit policy

The raw VASP mode set remains unchanged. `VibrationalModePolicy` determines which modes may enter
harmonic thermochemistry.

- `REJECT_ANY` fails when any imaginary VASP mode exists.
- `EXCLUDE_EXPLICIT` requires every accepted policy exclusion to be represented by the original
  one-based VASP mode index.
- real modes below the configured cutoff are either rejected or explicitly excluded according to
  `LowFrequencyPolicy`.
- constrained-mode exclusions are allowed only when explicitly declared.
- translation/rotation exclusions are rejected because they are gas semantics.

The resulting `ThermochemistryModeSelection` is persisted as result evidence and must exactly match
the exclusions in the thermochemistry identity.

### 5. Harmonic formulas are evaluated directly from parser mode energies

For each accepted real mode with quantum energy `epsilon = h nu`:

`ZPE_i = epsilon / 2`

`U_vib,thermal,i = epsilon / (exp(epsilon / k_B T) - 1)`

`S_vib,i = k_B [x/(exp(x)-1) - ln(1-exp(-x))]`, where `x = epsilon/(k_B T)`.

Block 2 stores the sums separately as ZPE, vibrational thermal energy, and vibrational entropy.
It does not collapse them into one opaque correction.

### 6. No quasi-RRHO, hindered-rotor, or empirical low-frequency replacement is implicit

The MVP harmonic adapter does not apply a frequency floor, absolute-value an imaginary frequency,
replace a low-frequency oscillator with a free rotor, or invoke a quasi-RRHO model. Such methods
would require a separately versioned scientific policy in a later Block.

### 7. Durable result uses the existing Analysis/Artifact/provenance model

Materialization creates:

`THERMOCHEMISTRY Analysis -> DERIVED_DATASET Artifact`.

The Analysis source receipt binds the exact Calculation, StructureSnapshot, MethodFingerprint,
parsed-result Analysis, parsed-result Artifact SHA-256, canonical VASP result hash, and complete
thermochemistry identity.

SCIENTIFIC dependencies are recorded from:

- Calculation -> Thermochemistry Analysis;
- MethodFingerprint -> Thermochemistry Analysis;
- StructureSnapshot -> Thermochemistry Analysis;
- RESULT_PARSE Analysis -> Thermochemistry Analysis;
- PARSED_RESULT Artifact -> Thermochemistry Analysis;
- Thermochemistry Analysis -> derived thermochemistry Artifact.

The existing FreshnessEngine remains authoritative; no thermochemistry-specific state machine or
freshness implementation is introduced.

### 8. Corrections remain outside Block 2

A Block 2 identity carrying empirical/reference/solvation/phase/user corrections is rejected.
Explicit correction policies and gas/reference construction belong to Block 4 so that raw harmonic
terms remain distinguishable from later reference corrections.

### 9. Persistence and schema boundaries remain unchanged

The output Artifact is canonical JSON under the Analysis-owned project path with SHA-256 and byte
size. `SCHEMA_VERSION` remains 3 and no runtime dependency is added.

## Consequences

ECatVASP now has a fail-closed bridge from durable VASP frequency facts to harmonic
surface/adsorbate Gibbs bookkeeping. Gas thermochemistry, gas-reference registries, CHE,
reaction stoichiometry, potential/pH views, and electrocatalysis descriptors remain downstream
Blocks and cannot leak assumptions back into the parser or Block 2 harmonic layer.
