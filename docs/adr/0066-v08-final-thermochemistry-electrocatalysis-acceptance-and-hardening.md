# ADR-066: v0.8 Final Thermochemistry and Electrocatalysis Acceptance and Hardening

- Status: Accepted
- Date: 2026-09-06
- Scope: v0.8 Block 9 final E2E acceptance, fail-closed hardening, and scope lock

## Context

ADR-057 defined the v0.8 thermochemistry/electrocatalysis architecture and its final acceptance
criteria. Blocks 1–8 subsequently implemented parameter-complete thermochemistry identities,
harmonic surface/adsorbate thermochemistry, ideal-gas molecular references, explicit reference
corrections, CHE potential/pH semantics, one generic signed reaction/pathway evaluator,
electrocatalytic descriptors and presets, durable requested-condition reaction diagrams, and pure
thermochemistry/reaction reconciliation over the existing SCIENTIFIC freshness graph.

Block 9 must prove that these independently frozen layers compose into one reproducible scientific
chain. It must not add a second persisted workflow/acceptance state machine, another schema version,
a reaction-specific evaluator, hidden reference data, or plotting authority merely to declare v0.8
complete.

## Decision

### 1. Final acceptance is test-backed rather than a new persisted subsystem

v0.8 final acceptance is established by the complete repository test suite plus an explicit
cross-layer E2E scenario. No `V08Acceptance` domain entity, database table, workflow generation,
parallel freshness engine, or persisted reaction lifecycle is introduced.

The final cross-layer scenario exercises the durable chain:

`FREQUENCY/GAS_FREQUENCY Calculation -> exact OUTCAR -> RESULT_PARSE -> THERMOCHEMISTRY -> bound H2 reference -> CHE -> generic HER pathway -> descriptors -> REACTION_DIAGRAM -> ProjectStore reopen -> thermochemistry/reaction reconciliation`.

The surface, adsorbate, and H2 inputs each start from an exact converged Calculation and exact parsed
VASP energy/frequency facts. The reaction diagram must be reconstructed from exact scientific
sources rather than from a manually supplied free-energy table.

### 2. Final E2E invariants

`tests/test_v08_e2e_acceptance.py` locks the following combined invariants:

- the clean surface, H* adsorbate, and H2 reference originate from explicit converged frequency
  Calculations and exact OUTCAR source hashes;
- surface/adsorbate harmonic thermochemistry consumes an explicit electronic-energy semantic and
  explicit vibrational policy;
- the H2 ideal-gas reference retains explicit species, geometry, symmetry, masses, pressure, and
  standard-state identity;
- CHE is constructed from the exact bound H2 thermochemistry reference under explicit SHE/RHE and
  pH semantics;
- the HER preset compiles to the generic Block 6 stoichiometric pathway and is evaluated by
  `evaluate_reaction_pathway()`;
- limiting potential, reversible potential, and HER `Delta G_H*` remain definition-bound results;
- the durable requested-condition reaction diagram reproduces the deterministic Block 7 affine
  potential/pH view and retains exact source/descriptor hashes;
- ProjectStore save/open preserves the complete Calculation/Artifact/Analysis/provenance/dependency
  chain without adding a v0.8-specific persisted state model;
- repeated reconciliation over unchanged reopened state is deterministic and hash-identical;
- an exact raw OUTCAR scientific-hash drift propagates through RESULT_PARSE and THERMOCHEMISTRY to
  the downstream REACTION_DIAGRAM as `STALE/BLOCKED`;
- a thermochemistry Analysis scientific-identity/policy hash drift likewise invalidates freshness of
  the downstream reaction diagram.

### 3. HER, ORR, OER, and CO2-to-CO share one evaluator

Representative HER, associative 4e ORR, explicit-direction associative 4e OER, and 2e CO2-to-CO
presets are compiled independently, but all are evaluated by the same
`evaluate_reaction_pathway()` implementation. No reaction-family name selects an alternate Delta G
formula, CHE sign convention, or hidden reference table.

OER remains an explicit oxidation pathway rather than a string reversal of ORR. CO2-to-CO retains
its potential-independent CO-release step. The signed convention remains universally
`Delta G = sum(nu_i G_i)`, with products positive and reactants negative.

### 4. Public runtime enum surfaces fail closed

Final review identified a dynamic-runtime gap at newly exposed Block 8 constructor surfaces. Python
annotations alone do not prevent a caller from passing a plain string that compares equal to a
`StrEnum` member. Block 9 therefore requires the public reaction-diagram and reconciliation
constructors to reject string lookalikes explicitly.

The public boundary requires actual enum instances for:

- `ReactionDiagramDescriptorDefinition.kind`;
- `ReactionDiagramDescriptorDefinition.unit`;
- `ReactionDiagramSourceReceipt.source_kind`;
- `ThermochemistryAnalysisRequirement.analysis_type`.

Unsupported dynamic values fail closed with the corresponding scientific contract error; they are
never silently reinterpreted as a supported descriptor, source family, or Analysis type. This is a
constructor-boundary hardening only and does not create a second implementation of reaction-diagram
or reconciliation logic.

### 5. ADR-057 final acceptance matrix

| ADR-057 final criterion | Acceptance evidence |
| --- | --- |
| Frequency Calculation and exact VASP energy/frequency facts trace into thermochemistry | `tests/test_harmonic_thermochemistry.py`, `tests/test_ideal_gas_thermochemistry.py`, `tests/test_v08_e2e_acceptance.py` |
| Gas references are explicit and provenance-bound rather than manual Gibbs scalars | `tests/test_ideal_gas_thermochemistry.py`, `tests/test_reference_corrections.py`, `tests/test_v08_e2e_acceptance.py` |
| SHE/RHE and pH semantics are explicit and non-duplicated | `tests/test_che_conditions.py`, `tests/test_electrocatalysis_descriptors.py`, `tests/test_v08_e2e_acceptance.py` |
| Generic signed stoichiometry evaluates electrocatalytic pathways | `tests/test_reaction_stoichiometry.py`, `tests/test_electrocatalysis_presets.py`, `tests/test_v08_e2e_acceptance.py` |
| HER/ORR/OER/CO2-to-CO representative presets share one evaluator | `tests/test_electrocatalysis_presets.py`, `tests/test_v08_e2e_acceptance.py` |
| Zero-condition and requested U/pH pathway energies are deterministic | `tests/test_electrocatalysis_descriptors.py`, `tests/test_v08_e2e_acceptance.py` |
| Canonical reaction diagram and descriptor definitions are durable scientific data | `tests/test_v08_reaction_diagram_reconciliation.py`, `tests/test_v08_e2e_acceptance.py` |
| ProjectStore reopen preserves the thermochemistry/reaction provenance chain | `tests/test_v08_reaction_diagram_reconciliation.py`, `tests/test_v08_e2e_acceptance.py` |
| Scientific source/policy drift propagates stale/blocked downstream | `tests/test_v08_reaction_diagram_reconciliation.py`, `tests/test_v08_e2e_acceptance.py` |
| No filename inference or second persisted workflow state machine is introduced | Blocks 1–8 contract tests, ADR-057 through ADR-065, and `tests/test_v08_e2e_acceptance.py` |
| Ruff, mypy strict, pytest 3.11/3.12/3.13, MatterViz remain green | exact-head PR CI and post-merge main push CI |

### 6. Final v0.8 scope lock

Block 9 does not add:

- charged-cell CHE, constant-potential or grand-canonical DFT;
- implicit/explicit solvation models or electric-field corrections;
- hindered rotors or quasi-RRHO;
- barriers, NEB, kinetics, or microkinetics;
- automatic empirical correction databases;
- full Pourbaix phase-diagram construction;
- plotting or GUI authority over scientific reaction data;
- a scheduler/backend change;
- a new runtime dependency;
- a schema migration or a second workflow/reconciliation persistence model.

`SCHEMA_VERSION` remains 3 and the development package remains `0.8.0.dev0`. Routine completion of
v0.8 does not create a tag, GitHub Release, or PyPI publication.

### 7. Completion rule

Block 9 and v0.8 are accepted only after:

1. the Block 9 PR exact head passes Ruff, mypy strict, pytest, Python 3.11/3.12/3.13, and MatterViz;
2. architecture/scientific self-review finds no blocking scope, provenance, identity, or CHE/reaction
   semantics issue;
3. the PR head remains unchanged, main has not drifted, reviews/threads contain no blocker, and the
   exact-head merge guard passes;
4. the PR is squash-merged using the guarded exact head; and
5. the resulting main push CI completes successfully on the exact merge SHA.

Only then is v0.8 considered stable and complete.

## Consequences

v0.8 closes with a reproducible scientific contract rather than another stateful subsystem. The
final E2E path demonstrates that exact VASP frequency facts, thermochemistry, molecular references,
CHE, generic stoichiometry, electrocatalytic descriptors, durable reaction diagrams, ProjectStore
reopen, and SCIENTIFIC freshness compose correctly while preserving all v0.1–v0.7 boundaries.
