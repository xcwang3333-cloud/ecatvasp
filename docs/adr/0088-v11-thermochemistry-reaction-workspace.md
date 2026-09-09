# ADR-088: v1.1 Thermochemistry & Reaction Workspace

- Status: Accepted
- Date: 2026-09-09
- Scope: v1.1 Block 7 — Thermochemistry & Reaction Workspace

## Context

ADR-057 through ADR-066 froze the v0.8 thermochemistry and electrocatalysis scientific contracts.
The repository already contains parameter-complete thermochemistry identities, harmonic
surface/adsorbate thermochemistry, ideal-gas molecular references, explicit reference correction
policies, SHE/RHE CHE semantics, one generic signed stoichiometric reaction evaluator,
HER/ORR/OER/CO2-to-CO preset compilers, limiting/reversible-potential and adsorption descriptors,
durable requested-condition reaction-diagram datasets, and thermochemistry/reaction reconciliation
through the shared SCIENTIFIC freshness graph.

ADR-086 defines v1.1 as productization of those existing authorities through typed desktop workflow
surfaces while retaining `ProjectStore` as the sole durable project authority, Python as the sole
scientific authority, frozen IPC v1 compatibility, `SCHEMA_VERSION = 3`, and no new identity-changing
physics.

Block 7 therefore does not need another thermochemistry engine, CHE implementation, reaction-family
formula set, or plotting-derived scientific state. It needs a task-oriented application boundary that
resolves exact current scientific evidence from `ProjectStore`, invokes the frozen v0.8 authorities,
and returns human-facing thermochemistry and reaction facts without manual UUID/hash transfer or
manual energy transcription.

## Decision

### 1. Existing v0.8 thermochemistry/reaction contracts remain authoritative

Block 7 reuses, rather than reimplements:

- `ThermochemistryIdentity`, `ThermochemicalConditions`, `VibrationalModePolicy`, and component-resolved
  `ThermochemistryResult`;
- `materialize_harmonic_thermochemistry` for fixed-cell surface/adsorbate frequency results;
- `GasReferenceDefinition`, `INITIAL_GAS_REFERENCE_REGISTRY`, and
  `materialize_ideal_gas_thermochemistry` for H2/H2O/O2/CO/CO2 references;
- explicit `ReferenceCorrectionPolicy` / `materialize_reference_thermochemistry`;
- `CHEConditions` and the frozen SHE/RHE proton-electron chemical-potential semantics;
- the single generic `evaluate_reaction_pathway` signed-stoichiometry evaluator;
- the four explicit preset compilers for HER, associative ORR, associative OER, and CO2-to-CO;
- `evaluate_potential_dependent_pathway_view`, limiting/reversible-potential, OER-overpotential, and
  HER `Delta G_H*` descriptor authorities;
- `materialize_reaction_diagram` for durable requested-condition scientific datasets;
- `ThermochemistryAnalysisRequirement` reconciliation over the existing `FreshnessEngine`.

No desktop or Block 7 application module duplicates those equations, signs, descriptor definitions,
scientific hashes, or freshness rules.

### 2. Block 7 introduces a transient project-scoped application workspace

A `ProjectThermochemistryApplicationService` may reopen `ProjectStore`, resolve exact current
Calculations, parsed-result Analyses/Artifacts, StructureSnapshots, MethodFingerprints, durable
thermochemistry Analyses/Artifacts, and reaction-diagram dependencies, invoke existing v0.8
materializers/evaluators, persist the existing generic `Analysis`/`Artifact`/provenance/dependency
graph, and return display-ready facts.

It does not introduce `ThermochemistryWorkspace`, `ReactionStudy`, `FreeEnergyPlot`, `Sweep`,
`Ranking`, `Task`, `Session`, or any other new durable top-level entity. `SCHEMA_VERSION` remains 3.

### 3. Desktop IPC v2 remains operation-specific and fail closed

Block 7 extends `ecatvasp-desktop-ipc-v2` with typed operations only. The intended product surface is:

- `thermochemistry_catalog` — page-scoped inventory/readiness/freshness for supported frequency
  sources, gas-reference sources, durable thermochemistry Analyses, and reaction diagrams;
- `materialize_harmonic_thermochemistry` — create/reuse exact fixed-cell surface/adsorbate
  thermochemistry from one managed scientifically converged frequency Calculation;
- `materialize_gas_reference` — create/reuse one explicit ideal-gas reference from one managed
  scientifically converged gas-frequency Calculation and explicit molecular/thermochemical metadata;
- `thermochemistry_view` — reopen one exact durable THERMOCHEMISTRY Analysis through its canonical
  artifact contract and return component/mode/reference facts;
- `reaction_preview` — compile one supported explicit preset and evaluate a pure requested-condition
  pathway/descriptor preview using exact durable source Analyses selected by role;
- `materialize_reaction_diagram` — re-resolve the preview sources server-side and persist/reuse one
  canonical requested-condition REACTION_DIAGRAM dataset through the frozen v0.8 materializer;
- `reaction_diagram_view` — reopen one exact durable REACTION_DIAGRAM Analysis and return canonical
  ordered states, step energies, conditions, source receipts, and descriptor definitions.

No generic `reaction_payload`, arbitrary species-to-energy dictionary, user-supplied source hash,
artifact path, raw Gibbs table, shell command, or scientific verdict is accepted.

### 4. Harmonic thermochemistry resolves managed VASP evidence server-side

`materialize_harmonic_thermochemistry` accepts a `CalculationId` plus explicit thermochemistry policy
choices. Python must resolve:

- one current `FREQUENCY` Calculation in scientific `CONVERGED` state;
- its exact MethodFingerprint and input StructureSnapshot;
- one exact completed `RESULT_PARSE` Analysis for that Calculation generation;
- one exact local canonical parsed-result Artifact containing the frequency dataset;
- exact current artifact bytes/hash/size before materialization.

The frontend never supplies a parsed-result path, source hash, MethodFingerprint hash, or energy value.
Surface/adsorbate subject kind, electronic-energy semantic, temperature, frequency cutoff,
imaginary-mode policy, low-frequency policy, and explicit mode exclusions remain part of scientific
identity and therefore must be typed request fields.

Repeated materialization of the same exact source and parameter-complete identity must reuse the
existing durable Analysis rather than append a duplicate.

### 5. Gas references remain explicit scientific references, not a scalar registry

`materialize_gas_reference` uses the existing initial species vocabulary H2/H2O/O2/CO/CO2 but does
not turn that vocabulary into manually stored Gibbs numbers.

The request selects one exact `GAS_FREQUENCY` Calculation and explicitly supplies the v0.8-required
molecular identity fields that cannot be inferred from filenames: gas species, standard state,
actual pressure, rigid-rotor geometry class, symmetry number, spin multiplicity, electronic-entropy
policy, atom-UID-bound masses, vibrational policy, and energy semantic.

Block 7 may provide UI defaults only when they are presentation suggestions. Scientific materialization
still consumes explicit typed values and the v0.8 constructors remain the validation authority.

### 6. Reference corrections remain visible, typed, and separate

Any supported O2/water/reference adjustment remains an explicit versioned correction layer over an
exact durable gas reference. The UI may expose known configured policies, but must never silently
replace a raw DFT gas result or inject a hidden constant into reaction evaluation.

Automatic empirical correction databases and automatic experimental reference lookup remain outside
Block 7.

### 7. CHE condition handling remains a Python scientific operation

The desktop may let the user choose potential, pH, temperature, and SHE/RHE reference, but it does not
calculate proton-electron chemical potentials or convert reference scales itself.

Python constructs `CHEConditions` and applies the existing v0.8 semantics. In particular, RHE must not
receive a second pH free-energy term. Unsupported or inconsistent conditions fail closed before a
reaction preview or materialization is returned.

### 8. Supported reaction families are explicit preset compilers over one evaluator

Block 7 exposes only the already-frozen initial preset vocabulary:

- HER Volmer-Heyrovsky;
- associative four-electron ORR;
- explicit-direction associative four-electron OER;
- two-electron CO2-to-CO through COOH* and CO*.

The application layer maps user-selected durable source Analyses to the fixed scientific roles required
by the selected preset and passes the resulting explicit species keys into the existing compiler.
Reaction-family or preset names never select alternate `Delta G` equations, hidden signs, or hidden
reversible potentials.

A new mechanism, stoichiometry, or physics model is not added merely as a desktop option; it requires a
separate scientific-core decision.

### 9. Exact source binding is resolved in Python

A reaction preview/materialization request selects durable source Analyses by explicit preset role,
not by manually typing energies. Python must:

- require each selected Analysis to be current `THERMOCHEMISTRY` and scientifically fresh/satisfied;
- resolve exactly one canonical output Artifact produced by that Analysis;
- verify local bytes/hash/size and canonical payload identity;
- construct the existing typed `ThermochemistryReactionSource` or
  `MolecularReferenceReactionSource` from the canonical result;
- construct the CHE source from exact requested conditions and an exact bound H2 reference;
- ensure every required preset role is present exactly once and no unsupported extra role is used.

No filename or label inference substitutes for preset-role selection.

### 10. Reaction preview is ephemeral; durable diagrams remain scientific datasets

`reaction_preview` is a pure, non-persisted application result. It may expose:

- compiled preset/family identity;
- baseline and requested CHE conditions;
- ordered state keys/labels;
- baseline/requested cumulative free energies;
- per-step `Delta G`, CHE coefficient, and potential slope;
- limiting/reversible potential where definitionally valid;
- OER theoretical overpotential or HER `Delta G_H*` where definitionally valid;
- determining/tied step identities;
- exact source identities and freshness facts.

`materialize_reaction_diagram` does not persist an arbitrary preview table. It re-resolves the exact
sources and calls the frozen v0.8 `materialize_reaction_diagram`, which re-evaluates the pathway and
writes the canonical `REACTION_DIAGRAM` `DERIVED_DATASET`.

### 11. Frontend plotting is presentation only

The desktop reaction workspace may render free-energy diagrams from the canonical ordered state and
step datasets returned by Python. It may change layout, axis range, labeling, visibility, and other
visual presentation properties.

It may not recompute cumulative free energies, CHE shifts, limiting potential, reversible potential,
overpotential, adsorption free energy, descriptor identity, source freshness, or pathway identity.

### 12. Freshness and source drift remain Python authority

Catalog and view responses derive readiness/freshness from the existing thermochemistry reconciliation
and SCIENTIFIC dependency DAG. Local Artifact bytes are observed before presenting a result as current.
An upstream parsed-result, thermochemistry, correction, reference, or reaction-source drift must surface
as stale/blocked/invalid through Python rather than being inferred from filenames or timestamps in the
frontend.

### 13. Project switching and mutation responses require monotonic guards

The Svelte workspace follows the Block 5/6 monotonic request-generation pattern. A -> B -> A project
switch, same-project refresh, stale catalog/view response, stale thermochemistry materialization
receipt, stale gas-reference receipt, stale reaction preview, or stale diagram materialization receipt
must not overwrite the active project state.

This remains transient UI concurrency state and is not persisted to ProjectStore.

### 14. Frozen boundaries

Block 7 does not change:

- desktop IPC v1 operation semantics;
- `SCHEMA_VERSION = 3`;
- `ProjectStore` authority;
- Calculation/ExecutionAttempt/RemoteJob separation;
- v0.8 thermochemistry, gas-reference, correction, CHE, reaction, descriptor, or diagram semantics;
- v0.8 scientific identity dimensions;
- package release state.

Block 7 also does not add charged-cell CHE, constant-potential/grand-canonical DFT, implicit or explicit
solvation physics, electric-field corrections, hindered rotors/quasi-RRHO, barriers/NEB, kinetics,
microkinetics, full Pourbaix construction, automatic empirical correction databases, Study/sweep/
ranking semantics, or hidden experimental constants.

## Acceptance

Block 7 is accepted only when tests demonstrate:

1. operation-specific IPC v2 decoding rejects unknown fields and contains no generic payload/path/hash/
   raw-energy escape hatch;
2. harmonic surface/adsorbate materialization resolves exact managed frequency scientific evidence and
   reuses exact duplicates;
3. gas-reference materialization requires explicit species/molecular/pressure/standard-state identity
   and reuses exact duplicates;
4. canonical thermochemistry view exposes component-resolved energies/entropy/mode selection without
   frontend scientific recomputation;
5. SHE/RHE/pH semantics remain Python-authoritative and RHE cannot receive a duplicate pH correction;
6. HER/ORR/OER/CO2-to-CO presets compile through the single generic evaluator with exact role binding;
7. limiting/reversible potential, OER overpotential, and HER `Delta G_H*` remain definition-bound
   Python results rather than UI formulas;
8. durable reaction-diagram materialization re-resolves exact canonical source Analyses/Artifacts and
   cannot persist an arbitrary frontend energy table;
9. reaction-diagram view preserves ordered state/step data, requested conditions, descriptor
   definitions, and exact source receipts;
10. source byte/hash/policy drift propagates stale/blocked through the shared freshness authority;
11. project-switch and stale mutation/preview responses cannot overwrite current desktop state;
12. IPC v1, schema3, package development state, and all deferred-physics boundaries remain frozen;
13. Ruff, strict mypy, pytest on Python 3.11/3.12/3.13, MatterViz, desktop typecheck/tests/build, Tauri
    transport guards, Windows frozen-backend E2E, NSIS packaging/output verification, and installer
    upload all pass on the exact PR head;
14. anchored self-review, final merge guard, expected-head squash merge, and exact-main post-merge CI
    all pass before Block 7 is frozen.

## Consequences

Block 7 turns the complete v0.8 thermochemistry/electrocatalysis layer into an operable desktop
research workflow without adding another scientific engine or asking the user to transcribe energies
into an external spreadsheet. Exact frequency calculations, thermochemistry, gas references, CHE,
reaction presets, descriptors, and canonical free-energy diagrams remain traceable through the existing
ProjectStore/provenance/freshness graph while the desktop is limited to task orchestration and
presentation.