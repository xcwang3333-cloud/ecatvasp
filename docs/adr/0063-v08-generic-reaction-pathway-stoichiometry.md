# ADR-063: v0.8 Generic Reaction and Pathway Stoichiometry

- Status: Accepted
- Date: 2026-09-05

## Context

Blocks 2-5 establish surface/adsorbate thermochemistry, molecular references, explicit correction
layers, and the CHE proton-electron reservoir. Block 6 must combine those sources into reaction free
energies without introducing reaction-specific sign rules or inferring intermediates from names.

HER, ORR, OER, and CO2RR must eventually be presets over one evaluator rather than separate scientific
implementations.

## Decision

### 1. One signed stoichiometric equation is authoritative

Every reaction step is evaluated as:

`Delta G = sum_i(nu_i G_i)`

with products positive and reactants negative.

`StoichiometricTerm` stores an explicit finite non-zero coefficient and a scientific `species_key`.
Fractional coefficients are valid. The evaluator does not infer coefficients from chemical formulas,
filenames, reaction labels, or pathway names.

A `ReactionStepDefinition` requires at least one positive and one negative term. Duplicate species
keys are rejected: callers must first aggregate a species into one net coefficient.

### 2. Scientific energy sources are typed and explicit

Block 6 accepts three source families:

- `ThermochemistryReactionSource` for non-gas surface/adsorbate thermochemistry;
- `MolecularReferenceReactionSource` for an explicitly species-bound gas reference and optional
  Block 4 corrected reference;
- `CHEReactionSource` for one Block 5 proton-electron chemical-potential event.

Raw gas `ThermochemistryResult` objects cannot bypass molecular species binding. A corrected molecular
reference must derive from the exact bound raw result and retain the same molecular reference identity.

Each contribution records source kind, exact scientific source hash, Gibbs energy, coefficient, and
signed contribution.

### 3. Source registries are exact, not permissive

For a reaction step or pathway, the supplied source keys must exactly equal the keys referenced by
its stoichiometry. Missing sources fail. Extra unused sources also fail rather than being silently
ignored. Duplicate source keys fail.

This prevents stale or accidentally selected reference objects from being present in an evaluation
without participating in its scientific identity.

### 4. Thermodynamic conditions must be coherent

All sources in one evaluation must have exactly the same temperature. Block 6 does not extrapolate
thermochemistry between temperatures.

When multiple CHE sources occur in an evaluation, they must share the exact Block 5
`CHEConditions.parameters_hash`. Mixing SHE/RHE conditions, potentials, pH values, or temperatures in
one reaction result therefore fails closed.

Non-electrochemical sources carry no CHE condition hash. The pathway result records the common CHE
condition identity when one is present.

### 5. Potential sign comes from stoichiometry

Block 6 never applies a reaction-name-specific potential sign.

A CHE source already contains the chemical potential of one `H+ + e-` event. Consuming it uses a
negative stoichiometric coefficient; producing it uses a positive coefficient. Consequently the
potential slope of a reaction step arises from `nu * mu_CHE` and no HER/ORR/OER/CO2RR mnemonic is
needed.

### 6. Pathways are explicit directed graphs reduced to an ordered path

`ReactionPathwayDefinition` stores an ordered state-key sequence and exactly one explicit step between
each adjacent pair. Every step must declare matching `initial_state_key` and `final_state_key`.

State keys are ordered positions, not globally unique node identifiers. A non-adjacent state key may
repeat so that a catalytic pathway can explicitly return to its initial clean catalyst state after
product release. An individual step still cannot connect a state to itself, so consecutive identical
states remain invalid.

Reverse chemistry is another explicit pathway/step definition. OER is not represented by string
reversal of an ORR pathway, and no step direction is inferred from a label.

The pure pathway evaluator returns ordered step results and cumulative state free energies beginning
at zero. Durable diagram materialization is deferred to Block 8.

### 7. Adsorption free energy is not a separate scientific formula

`evaluate_adsorption_free_energy()` is a convenience compiler into the same generic evaluator.

For an adsorbed state, clean surface, and explicit reference terms it constructs:

- `+1 * G(surface+adsorbate)`;
- `-1 * G(clean surface)`;
- the caller-declared signed reference coefficients.

The resulting `ReactionStepDefinition` and `ReactionStepResult` are identical to those produced by
manually declaring the same generic stoichiometry. There is no second adsorption-specific energy
engine.

### 8. Pure results are component-resolved and self-validating

`ReactionTermContribution` verifies `contribution = nu * G`.

`ReactionStepResult` verifies its exact contribution sum and retains:

- reaction-definition hash;
- ordered state direction;
- temperature;
- optional CHE condition hash;
- every source hash and signed contribution;
- final `Delta G` and deterministic result hash.

`ReactionPathwayResult` verifies that cumulative state energies are the ordered sum of step energies,
that all step temperatures are identical, and that all non-null CHE condition hashes are identical.
This preserves the evaluator's condition-coherence guarantees even if a result object is constructed
directly rather than through the pathway evaluator.

### 9. Persistence and descriptors remain downstream

Block 6 is a deterministic scientific evaluation layer. It does not create a new ProjectBundle
entity, workflow state machine, or durable reaction diagram Artifact.

Block 7 will derive potential-dependent pathway views, limiting potentials, overpotentials, and HER
specific descriptors from these explicit step definitions. Block 8 will own durable reaction-diagram
materialization and reconciliation/freshness gates.

`SCHEMA_VERSION` remains 3 and no runtime dependency is added.

## Fail-closed boundaries

Block 6 rejects:

- zero/non-finite stoichiometric coefficients;
- duplicate unaggregated species terms;
- steps with no product or no reactant;
- raw gas thermochemistry used without molecular reference binding;
- corrected molecular references with mismatched raw lineage;
- missing, extra, or duplicate scientific sources;
- mixed temperatures;
- multiple CHE sources with different exact electrochemical conditions;
- pathway steps that do not match adjacent directed state keys;
- directly constructed pathway results with mixed step temperatures or CHE conditions;
- adsorption requests without explicit reference terms.

Block 6 does not infer reaction balancing, atom balance, charge balance, reaction direction,
intermediate identity, coverage normalization, potential-dependent descriptors, kinetic barriers,
rate constants, or microkinetics.

## Consequences

Surface/adsorbate thermochemistry, corrected molecular references, and CHE now share one signed
stoichiometric free-energy evaluator. Reaction-family presets can be added later without creating
parallel scientific engines, and all potential dependence remains traceable to explicit CHE
stoichiometry rather than hidden sign conventions.
