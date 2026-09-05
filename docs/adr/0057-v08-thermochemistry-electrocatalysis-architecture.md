# ADR-057: v0.8 Thermochemistry and Electrocatalysis Free-Energy Architecture

- Status: Accepted
- Date: 2026-09-05

## Context

v0.5 preserves explicit VASP energy semantics and raw finite-difference vibrational modes without
making a thermodynamic verdict. v0.6 owns the persisted Calculation-only scientific workflow, and
v0.7 establishes durable Analysis/Artifact materialization plus SCIENTIFIC freshness and pure
analysis reconciliation. v0.8 must add thermochemistry, gas references, CHE, reaction free energies,
and electrocatalytic descriptors without collapsing those existing boundaries.

## Decision

### 1. Thermochemistry is an Analysis, not a parser extension or Calculation type

The source chain is:

`frequency Calculation -> RESULT_PARSE Artifact -> THERMOCHEMISTRY Analysis -> DERIVED_DATASET`

Raw frequencies, VASP energy fields, and atom-UID-addressed eigenvectors remain parser facts.
Accepted modes, imaginary/low-frequency treatment, thermodynamic conditions, gas molecular model,
and any correction policy belong to thermochemistry Analysis identity. The resulting dataset keeps
all free-energy components separately and may expose assembled Gibbs energy only as a derived value.

### 2. Scientific identity is parameter-complete

Any choice that can change a thermochemical result participates in `Analysis.parameters_hash`,
including temperature, actual pressure, standard state, selected VASP electronic-energy semantic,
frequency cutoff, imaginary-mode policy, explicit exclusions, constrained-mode treatment, molecular
linearity, symmetry number, spin multiplicity/electronic-entropy policy, and correction-policy
identity/version.

The existing `Analysis` scientific hash already includes analysis type, exact ordered input Artifact
ids, tool/version, and parameters hash. No parallel identity mechanism is introduced.

### 3. Standard states are explicit

v0.8 distinguishes fixed-cell surface thermochemistry, ideal gas referenced to 1 bar, and ideal gas
referenced to 1 atm. Actual evaluation pressure is independent metadata and must be explicit for gas
thermochemistry. No conversion between 1 bar and 1 atm occurs silently.

Canonical energy is eV per modeled reaction event/cell. Other molar-unit displays are deterministic
views and are not independent scientific results.

### 4. Gas references are provenance-bound thermochemistry materializations

H2, H2O, O2, CO, and CO2 form the initial registry vocabulary. A registry entry is not a manually
stored final Gibbs number. Each concrete gas reference must retain exact electronic-energy source,
frequency result, MethodFingerprint dependency, thermochemistry parameters, component breakdown,
and final derived dataset hash.

Rigid-rotor/ideal-gas treatment requires explicit linear/nonlinear classification and symmetry
number. Spin multiplicity is explicit; electronic entropy is either explicitly neglected or computed
from an explicit supported model. Molecular metadata is never inferred from filenames.

### 5. Corrections are additive, typed, visible, and versioned

O2 corrections, water phase/reference strategies, empirical DFT corrections, experimental
reference terms, and later solvation corrections never overwrite raw DFT energy or a gas reference.
They are explicit correction terms/policies included in scientific identity and output component
breakdown. Unsupported or provenance-incomplete corrections fail closed.

### 6. CHE uses one reference convention at a time

For a proton-electron pair at potential versus SHE:

`mu(H+ + e-) = 1/2 G(H2) - e U_SHE - k_B T ln(10) pH`

with

`U_SHE = U_RHE - (k_B T/e) ln(10) pH`.

Therefore, when potential is supplied versus RHE:

`mu(H+ + e-) = 1/2 G(H2) - e U_RHE`.

An RHE condition must not receive an additional pH free-energy correction. Potential is stored in
V and energy in eV, with electron/proton-electron stoichiometry explicit. Reaction free energy always
uses `Delta G = sum(nu_i G_i)` with products positive and reactants negative, so the sign of a CHE
potential term follows stoichiometry rather than a reaction-name special case.

### 7. Reaction free energy uses a generic stoichiometric DAG

HER, ORR, OER, and CO2RR are presets over one generic reaction/pathway contract. A state references
explicit surface/adsorbate thermochemistry outputs, gas-reference outputs, water/reference terms,
and CHE reservoir stoichiometry. Intermediates are not inferred from filenames or directory names.

Each step is directed and explicit; OER is not represented as string reversal of an ORR path.
Adsorption free energy is a one-step use of the same stoichiometric evaluator:

`Delta G_ads = G(surface+adsorbate) - G(surface) - sum(nu_i G(reference_i))`

with the reference stoichiometry explicitly declared.

### 8. Canonical zero-condition data and potential/pH views are separate

The canonical pathway materialization is evaluated at its declared reference condition, normally
U=0 and pH=0 for a SHE-form CHE baseline. Potential/pH sweeps are deterministic affine views by
default and do not create hundreds of persisted Analyses. A specific condition is materialized only
when requested as a durable dataset; then U, pH, T, potential reference, and source hashes enter its
identity.

### 9. Electrocatalytic descriptors retain their definition

Limiting potential and potential-determining step are derived by solving the explicit step-energy
constraints using each step's potential slope; v0.8 does not depend on a reaction-specific sign
mnemonic. OER theoretical overpotential is the minimum applied potential making all declared
oxidation steps thermodynamically downhill minus the reversible potential derived from or explicitly
bound to the same reference state. HER `Delta G_H*` remains an explicitly defined adsorption free
energy, not a bare scalar.

Every descriptor retains pathway identity, exact step energies, determining step, potential
reference, sign convention, conditions, and provenance.

### 10. Diagram data is scientific data; plotting is not

The canonical reaction free-energy diagram dataset contains ordered state keys, labels, cumulative
free energies, step free energies, conditions, reaction/pathway identity, and source hashes. It is a
DERIVED_DATASET produced by Analysis. A future GUI/plotter consumes this dataset; matplotlib output
is never the authoritative scientific result.

### 11. Existing freshness and workflow semantics remain authoritative

Thermochemistry and reaction analyses use `Analysis`, `Artifact`, `AnalysisProducerRef`,
`ProvenanceRecord`, `DependencyRecord(kind=SCIENTIFIC)`, `FreshnessEngine`, and `ProjectStore`.
Frequency-result drift, electronic-energy/source drift, gas-reference drift, parameter drift,
correction-policy drift, CHE-condition drift, and pathway-definition drift propagate through the
same DAG as v0.7.

`ScientificWorkflowPlan` remains Calculation-only. Thermochemistry/reaction readiness will follow the
v0.7 pure reconciliation pattern and will not persist a second workflow state machine.

### 12. Schema and dependencies remain frozen for the MVP

Project `SCHEMA_VERSION` remains 3. New thermochemistry/reaction contracts are value objects stored
inside derived datasets or represented through existing Analysis parameters/provenance; no new
ProjectBundle top-level entity is required. No runtime dependency is added. Standard-library math
plus existing NumPy/ASE facilities are sufficient for the planned MVP.

## Fail-closed boundaries

v0.8 must fail rather than guess when any required energy semantic is missing, raw frequency source
is stale/ambiguous, an imaginary or near-zero mode violates the selected policy, constrained modes
are not explicitly identified, gas linearity/symmetry/multiplicity is missing, gas pressure or
standard state is missing, a correction lacks explicit policy identity, RHE is combined with a
second pH correction, pathway stoichiometry is incomplete, an intermediate is named only by a file,
coverage/normalization is ambiguous, or an upstream scientific hash cannot be resolved.

Charged-cell CHE, constant-potential/grand-canonical DFT, implicit or explicit solvation corrections,
electric-field corrections, explicit-solvent MD, hindered rotors/quasi-RRHO, kinetics/barriers, NEB,
microkinetics, and full Pourbaix phase-diagram construction are not silently approximated by the
MVP.

## Planned Blocks

1. Thermochemistry domain contracts and parameter-complete identity.
2. Frequency-to-thermochemistry materialization for harmonic surface/adsorbate modes.
3. Ideal-gas reference registry and translational/rotational/vibrational thermochemistry.
4. Explicit correction policies and gas/water reference strategies.
5. CHE conditions, SHE/RHE semantics, and potential/pH transformation.
6. Generic reaction/pathway stoichiometry and adsorption free energy.
7. Potential-dependent pathway views, limiting potential, OER overpotential, and HER descriptors.
8. Durable reaction-diagram dataset plus thermochemistry/reaction reconciliation and workflow gates.
9. Final end-to-end acceptance, reopen/freshness drift tests, and fail-closed hardening.

ADR-058 through ADR-066 are reserved for those Blocks in order.

## Final v0.8 acceptance

The phase is complete only when an end-to-end project can reopen from `ProjectStore`, trace a
frequency Calculation and exact VASP energy/frequency result through thermochemistry and gas
references into a generic electrocatalytic pathway, reproduce zero-condition and requested U/pH
energies deterministically, emit a canonical diagram dataset and descriptor definitions, and turn
all downstream outputs stale/invalid when any scientific source or policy hash drifts. HER, ORR,
OER, and CO2-to-CO representative pathways must share the same evaluator and must not depend on
filename inference.

## Deferred

GUI/plotting frameworks, scheduler backends, database frameworks, ML, microkinetics, barriers/NEB,
constant-potential or grand-canonical DFT, explicit-solvent MD, full Pourbaix engines, experimental
fitting, automatic empirical correction databases, and heavy workflow dependencies are deferred to
later phases.
