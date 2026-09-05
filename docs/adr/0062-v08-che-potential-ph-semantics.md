# ADR-062: v0.8 CHE Potential and pH Semantics

- Status: Accepted
- Date: 2026-09-05

## Context

Blocks 2-4 establish raw surface/gas thermochemistry and explicit corrected molecular references.
Block 5 must add the computational hydrogen electrode (CHE) without hiding reference corrections,
confusing SHE and RHE conventions, or applying the proton-activity pH term twice.

The CHE layer in this block owns electrochemical conditions and the chemical potential of one
`H+ + e-` reservoir event. Generic reaction/pathway stoichiometry remains a Block 6 concern.

## Decision

### 1. CHE consumes an explicit H2 thermochemistry reference

The hydrogen reservoir is represented by `CHEHydrogenReference`.

It contains a species-bound raw Block 3 H2 thermochemistry result and may optionally bind a Block 4
corrected H2 reference derived from that exact raw result. CHE never accepts a bare user-entered
`G(H2)` scalar as authoritative scientific input.

The following are verified before a corrected H2 reference can be used:

- the raw species is explicitly H2;
- corrected and raw molecular reference identities are identical;
- the corrected source-result hash equals the exact raw H2 result hash;
- the corrected source Gibbs energy equals the raw H2 Gibbs energy;
- the H2 reference remains an ideal-gas molecular reference phase.

When a valid correction layer is supplied, CHE uses its corrected Gibbs energy. The raw Block 3
thermochemistry remains unchanged.

### 2. Potential-reference and pH semantics are separate explicit fields

`CHEConditions` records:

- temperature in K;
- electrode potential in V;
- pH;
- potential reference: SHE or RHE;
- pH semantics.

The allowed combinations are deliberately strict:

- SHE + `EXPLICIT_ACTIVITY`;
- RHE + `INCLUDED_IN_RHE`.

SHE combined with RHE-style pH semantics fails. RHE combined with an explicit second pH correction
also fails. Invalid double counting is therefore rejected at condition construction rather than
being corrected heuristically downstream.

No arbitrary pH interval such as 0-14 is imposed. Any finite real pH is representable because
nonstandard activities can produce values outside that textbook interval.

### 3. CHE equations follow one convention at a time

For a proton-electron pair at potential versus SHE:

`mu(H+ + e-) = 1/2 G(H2) - e U_SHE - k_B T ln(10) pH`.

With energies in eV per proton-electron event and potential in V, the numerical single-electron
potential contribution is `-U_SHE` eV.

The SHE/RHE relation is:

`U_SHE = U_RHE - (k_B T / e) ln(10) pH`.

Therefore at potential versus RHE:

`mu(H+ + e-) = 1/2 G(H2) - e U_RHE`.

No additional pH energy term is applied to an RHE condition.

### 4. SHE/RHE transformations are deterministic views

Block 5 exposes deterministic conversions in both directions:

- `rhe_to_she_potential_v()`;
- `she_to_rhe_potential_v()`.

Both require explicit finite potential, finite pH, and positive finite temperature. Transforming an
RHE condition to the equivalent SHE potential and evaluating the corresponding explicit pH term must
produce the same proton-electron chemical potential.

### 5. Temperature identity is exact

CHE conditions must use the exact temperature of the bound H2 thermochemistry source. Block 5 does
not extrapolate H2 entropy or Gibbs energy between temperatures. A temperature mismatch fails closed
and requires a thermochemistry result evaluated at the requested temperature.

### 6. The CHE result remains component-resolved

`CHEProtonElectronChemicalPotential` records:

- CHE condition identity;
- exact H2 reference hash;
- H2 Gibbs energy used;
- `1/2 G(H2)` term;
- electrode-potential term;
- pH term;
- assembled chemical potential;
- deterministic result hash.

This prevents a downstream reaction evaluator from receiving an unexplained scalar reservoir value.

### 7. Reaction stoichiometry is explicitly deferred

Block 5 evaluates exactly one proton-electron reservoir event. It does not decide whether a pathway
step consumes or produces that event, does not infer electron count from a reaction name, and does
not calculate adsorption or reaction free energies.

Block 6 will apply signed explicit stoichiometric coefficients to CHE and molecular/surface reference
terms. Consequently the sign of a potential contribution will arise from reaction stoichiometry,
not from reaction-specific hard-coded mnemonics.

### 8. Condition sweeps are not persisted here

Potential and pH transformations are deterministic affine views. Block 5 therefore introduces no new
project entity, workflow state machine, or top-level persisted schema. Durable reaction-condition
materialization belongs downstream where a concrete reaction/pathway dataset requests it.

`SCHEMA_VERSION` remains 3 and no runtime dependency is added.

## Fail-closed boundaries

Block 5 rejects:

- non-H2 hydrogen references;
- corrected H2 references that do not derive from the exact bound raw H2 result;
- non-ideal-gas H2 reference phases;
- non-finite potential or pH;
- non-positive or non-finite temperature;
- CHE temperature that differs from H2 thermochemistry temperature;
- SHE with RHE-included pH semantics;
- RHE with an explicit second proton-activity pH correction.

Block 5 does not implement charged-cell thermodynamics, grand-canonical/constant-potential DFT,
interfacial field corrections, ion activities beyond the explicit pH convention, reaction
stoichiometry, limiting potentials, or overpotentials.

## Consequences

The CHE reservoir is now reproducible from an exact H2 thermochemistry lineage and an explicit
potential convention. SHE and RHE produce mathematically equivalent views when transformed at the
same temperature and pH, while an RHE double-pH correction is structurally invalid. Block 6 can now
consume this reservoir through generic signed reaction stoichiometry without embedding electrode
reference logic in individual reaction presets.
