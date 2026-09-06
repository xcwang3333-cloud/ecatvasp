# ADR-064: v0.8 Potential-Dependent Electrocatalysis Descriptors

- Status: Accepted
- Date: 2026-09-06

## Context

ADR-057 reserves Block 7 for potential-dependent pathway views and electrocatalytic descriptors.
ADR-063 establishes one generic signed stoichiometric evaluator in which all electrode-potential
dependence enters through explicit CHE source coefficients. Block 7 must derive useful HER/ORR/OER/
CO2RR thermodynamic views without reintroducing reaction-name sign mnemonics, hidden 1.23 V constants,
or a second reaction engine.

## Decision

### 1. Potential dependence remains an affine view over a Block 6 pathway result

For a step containing a net CHE stoichiometric coefficient `nu_CHE`, the CHE chemical potential has
`d mu_CHE / dU = -1 eV/V` for one proton-electron event. Therefore:

`d Delta G_step / dU = -nu_CHE`.

`PotentialDependentPathwayView` obtains every step slope only by summing explicit
`CHE_RESERVOIR` contribution coefficients in the baseline `ReactionStepResult`. Reaction family,
step label, filename, and reaction direction never choose the sign.

Changing potential reference between SHE and RHE is allowed only through explicit `CHEConditions`.
At fixed temperature the affine difference uses the same Block 5 semantics, so physically equivalent
SHE/RHE conditions give the same pathway energies and RHE never receives a second pH correction.
The view is pure and ephemeral in Block 7; durable requested-condition datasets remain Block 8.

### 2. Limiting potential is solved as an interval of explicit step inequalities

Every declared step must satisfy:

`Delta G_step(U) <= 0`.

A positive potential slope provides an upper bound on feasible potential; a negative slope provides
a lower bound; a zero-slope uphill step makes the pathway infeasible at every electrode potential.
The complete feasible interval is solved from all steps.

For a pathway with net CHE consumption (`sum nu_CHE < 0`), the reported limiting potential is the
finite maximum feasible potential. For net CHE production (`sum nu_CHE > 0`), it is the finite
minimum feasible potential. The determining step or tied steps are the exact constraints defining
that selected interval edge.

This rule is stoichiometric, not a hard-coded ORR/OER sign convention.

### 3. Reversible potential is derived from the same pathway reference state

The total pathway potential slope is `-sum nu_CHE`. When this slope is non-zero, the reversible
potential is the electrode potential at which the complete pathway free-energy change is zero:

`U_rev = U_0 - Delta G_total(U_0) / (d Delta G_total / dU)`.

The result retains pathway hash, baseline result hash, exact CHE conditions, net CHE coefficient,
total baseline free energy, slope, and reversible potential. Block 7 does not silently replace this
with a universal experimental constant. A later explicitly bound reversible-potential source would
need its own provenance contract rather than overwriting the derived result.

### 4. OER theoretical overpotential is a constrained oxidation descriptor

OER theoretical overpotential is evaluated only when the pathway has net CHE production and every
CHE-dependent step has oxidation-like potential slope. It is:

`eta_OER = U_lim - U_rev`.

The limiting and reversible potentials must use the exact same pathway, baseline result, and CHE
conditions. A negative overpotential is rejected because it indicates inconsistent descriptor inputs
or an invalid pathway/reference construction.

### 5. HER Delta G_H* remains an adsorption free energy

`Delta G_H*` is not accepted as an unbound scalar. The HER helper compiles the explicit reaction:

`* + 1/2 H2 -> H*`

through the Block 6 adsorption compiler and retains the full `ReactionStepResult` plus the exact H2
molecular-reference hash. Thus the descriptor remains traceable to clean-surface thermochemistry,
H* thermochemistry, molecular H2 reference thermochemistry/corrections, temperature, and all source
hashes.

### 6. Reaction-family presets are compilers, not scientific engines

Block 7 adds explicit initial preset vocabulary for:

- HER Volmer-Heyrovsky two-electron pathway;
- associative four-electron ORR to water;
- associative four-electron OER;
- two-electron CO2-to-CO through COOH* and CO*.

Each preset requires caller-supplied scientific species keys and compiles a concrete
`ReactionPathwayDefinition` containing signed stoichiometric terms. Role keys must be distinct so,
for example, gas-phase CO cannot silently alias adsorbed CO*. OER is declared independently in the
oxidation direction and is never produced by reversing ORR strings or step labels.

Preset identity is content-addressed together with the authoritative generic pathway-definition hash.
The generic Block 6 evaluator remains the only reaction free-energy implementation.

### 7. Scientific identity and persistence boundaries remain unchanged

Block 7 introduces value objects and pure deterministic results only. Descriptor hashes include exact
pathway/baseline identities, conditions, step constraints or adsorption result identity as applicable.
No new ProjectBundle entity, schema table, workflow state machine, plotting authority, or runtime
dependency is introduced.

`SCHEMA_VERSION` remains 3. Durable reaction-diagram datasets, requested-condition materialization,
reconciliation, SCIENTIFIC freshness gates, and workflow readiness are reserved for Block 8.

## Fail-closed boundaries

Block 7 fails rather than guesses when:

- no explicit CHE contribution exists for a potential-dependent descriptor;
- supplied baseline CHE conditions do not match the exact pathway result condition hash;
- target and baseline temperatures differ;
- a potential-independent step is uphill while solving a limiting potential;
- net CHE stoichiometry is zero for limiting/reversible-potential descriptors;
- the feasible potential interval is empty or lacks the finite edge required by the pathway direction;
- OER contains reduction-like CHE-dependent steps;
- OER limiting and reversible results differ in pathway, baseline source, or conditions;
- HER Delta G_H* lacks an explicit species-bound H2 molecular reference;
- a preset scientific role key is blank or aliases another distinct scientific role.

Block 7 does not infer atom/charge balancing, coverage normalization, alternative mechanisms,
solvation corrections, kinetics, barriers, exchange current, Tafel slope, microkinetics, constant-
potential DFT, Pourbaix stability, or experimental reversible potentials.

## Consequences

Potential sweeps and core electrocatalytic thermodynamic descriptors now inherit their signs and
condition dependence directly from the generic CHE stoichiometry established in Block 6. Initial HER,
ORR, OER, and CO2-to-CO mechanisms share one evaluator while still remaining explicit directed
scientific definitions. Block 8 can persist canonical diagram/descriptor datasets and reconcile their
freshness without changing the underlying thermodynamic equations.
