# ADR-060: v0.8 Ideal-Gas Reference Thermochemistry

- Status: Accepted
- Date: 2026-09-05

## Context

ADR-057 reserves Block 3 for ideal-gas reference thermochemistry. ADR-058 already requires explicit
gas geometry class, symmetry number, spin multiplicity, atom-UID-bound masses/isotopologues,
pressure and 1 bar/1 atm standard-state identity. ADR-059 materializes only fixed-cell
surface/adsorbate harmonic thermochemistry and deliberately rejects `GAS_FREQUENCY`.

Block 3 must construct gas references without turning H2, H2O, O2, CO or CO2 into manually entered
final Gibbs numbers and without hiding translational, rotational, vibrational, electronic or pV
terms inside one correction.

## Decision

### 1. The initial gas registry is molecular identity, not a table of energies

The initial explicit vocabulary is `H2`, `H2O`, `O2`, `CO`, and `CO2`.

A `GasReferenceDefinition` identifies one registry species and electronic-state label. The registry
contains no final Gibbs value, symmetry number, spin multiplicity, atomic masses, or thermochemical
correction. Those remain explicit inputs of each thermochemistry identity/materialization.

The registry composition is used only to fail closed when the exact `StructureSnapshot` does not
match the declared species.

### 2. Gas thermochemistry consumes only converged `GAS_FREQUENCY` facts

The durable source chain is:

`GAS_FREQUENCY Calculation -> RESULT_PARSE Analysis -> PARSED_RESULT Artifact -> THERMOCHEMISTRY Analysis -> DERIVED_DATASET`.

The Calculation must be scientifically `CONVERGED`; its MethodFingerprint, recipe,
StructureSnapshot, parsed-result Analysis and content-addressed Artifact must agree exactly.
Every molecular atom must have finite-difference frequency coverage.

### 3. Molecular metadata is explicit and validated, never inferred

`ThermochemistryIdentity.gas_model` supplies:

- `LINEAR` or `NONLINEAR` geometry class;
- symmetry number;
- spin multiplicity;
- exact atom-UID-bound masses and optional isotopologue labels.

Block 3 reconstructs a mass-weighted inertia tensor from the exact StructureSnapshot and checks that
the explicitly declared geometry class is compatible with its principal moments. It does not assign
a geometry class from the species name.

The supplied mass set must cover exactly the snapshot atom UIDs. ASE/default atomic masses are not
consulted. This keeps isotopic substitutions in scientific identity.

### 4. Rigid-body mode removal is explicit

A molecular gas frequency result contains `3N` raw VASP modes. The thermochemistry policy must
explicitly identify three translational modes and:

- two rotational modes for a linear molecule;
- three rotational modes for a nonlinear molecule.

The original one-based VASP mode indices are retained. No eigenvector classifier silently labels
translations or rotations. Remaining low-frequency or imaginary vibrational modes continue to obey
the explicit Block 1 policies. Constrained molecular frequency sets are rejected for the initial
registry.

Monatomic gas is outside the initial molecular registry and fails closed in Block 3.

### 5. Ideal-gas translational terms use the explicit evaluation pressure

For molecular mass `m`, temperature `T`, and actual pressure `p`, the one-particle translational
partition function is evaluated with `V = k_B T / p`:

`q_trans = (2*pi*m*k_B*T/h^2)^(3/2) * k_B*T/p`.

The stored components are:

`U_trans = 3/2 k_B T`

`S_trans = k_B [ln(q_trans) + 5/2]`.

The pressure is therefore scientifically active, rather than display-only metadata.

### 6. Rigid-rotor terms use explicit symmetry and exact moments of inertia

For a linear molecule with rotational moment `I` and symmetry number `sigma`:

`q_rot = 8*pi^2*I*k_B*T / (sigma*h^2)`

`U_rot = k_B T`

`S_rot = k_B [ln(q_rot) + 1]`.

For a nonlinear molecule with principal moments `I_A`, `I_B`, `I_C`:

`q_rot = sqrt(pi)/sigma * (8*pi^2*k_B*T/h^2)^(3/2) * sqrt(I_A*I_B*I_C)`

`U_rot = 3/2 k_B T`

`S_rot = k_B [ln(q_rot) + 3/2]`.

The implemented MVP uses the conventional rigid-rotor/high-temperature molecular partition
functions. Hindered rotors and quasi-RRHO remain explicitly deferred.

### 7. Vibrational terms remain harmonic and component-resolved

Accepted molecular vibrational modes use the same harmonic-oscillator equations frozen by ADR-059.
ZPE, thermal vibrational energy, and vibrational entropy remain separate result components.

### 8. Electronic entropy is policy-controlled

`ElectronicEntropyPolicy.NEGLECTED` gives zero electronic entropy.

`SPIN_DEGENERACY` gives:

`S_elec = k_B ln(g)`

where `g` is the explicitly supplied spin multiplicity. For example, an O2 triplet reference is
represented by explicit multiplicity 3 rather than by a species-name special case.

### 9. The ideal-gas pV component is explicit

For one modeled ideal-gas molecule/event:

`pV = k_B T`.

The canonical Gibbs assembly therefore remains the shared Block 1 expression:

`G = E_elec + ZPE + U_vib + U_trans + U_rot + pV - T(S_vib + S_trans + S_rot + S_elec)`.

No empirical/reference correction is applied in Block 3.

### 10. 1 bar and 1 atm remain distinct identities

`IDEAL_GAS_1_BAR` and `IDEAL_GAS_1_ATM` remain different standard-state conventions. Actual
pressure is also explicit and enters the translational entropy directly. A 1 bar reference normally
uses 100000 Pa and a 1 atm reference 101325 Pa, but Block 3 does not silently force or convert one
into the other because ADR-057 keeps evaluation pressure independent from standard-state metadata.

### 11. Existing provenance and freshness remain authoritative

The THERMOCHEMISTRY Analysis source receipt binds registry identity, Calculation,
StructureSnapshot, MethodFingerprint, parsed-result Analysis, parsed-result Artifact hash,
canonical VASP result hash, complete gas thermochemistry identity, and reconstructed rotor evidence.

SCIENTIFIC dependencies connect the existing upstream objects to the Analysis and the Analysis to
its derived dataset. No gas-specific workflow or freshness state machine is added.

### 12. Scope boundaries remain frozen

Block 3 does not apply O2 empirical corrections, water phase/reference strategies, experimental
references, solvation corrections, CHE, reaction stoichiometry, limiting potentials, overpotentials,
or plotting. Those remain downstream Blocks.

`SCHEMA_VERSION` remains 3 and no runtime dependency is added; NumPy was already part of the
project dependency set.

## Consequences

ECatVASP can now construct provenance-bound ideal-gas thermochemistry from first-principles energy,
frequency and geometry facts while preserving every statistical-mechanical contribution and every
identity choice needed for reproducibility. The next layer may add explicit reference/correction
policies without mutating these raw gas thermochemistry results.
