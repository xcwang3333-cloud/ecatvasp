# ADR-056: v0.7 Final Electronic Analysis Acceptance and Hardening

- Status: Accepted
- Date: 2026-09-05
- Scope: v0.7 Block 9 final E2E acceptance and scope lock

## Context

ADR-048 defined the v0.7 electronic-structure architecture and the final acceptance criteria. Blocks 1–8 then implemented canonical DOS/PDOS intake, durable DOS analysis materialization, Bader intake, charge-density difference, LOBSTER COHP/ICOHP intake, electronic descriptors, and pure electronic-analysis reconciliation on top of the existing v0.6 workflow/freshness model.

Block 9 must prove the combined system rather than create another product layer. In particular, acceptance must not introduce a second persisted workflow state machine, a new schema revision, or thermochemistry scope simply to report that v0.7 is complete.

## Decision

### 1. Final acceptance is test-backed, not a new persisted entity

v0.7 final acceptance is established by the complete repository test suite plus one explicit cross-layer E2E scenario. No `V07Acceptance` domain entity, persisted lifecycle state, workflow generation, database table, or additional freshness engine is introduced.

The final cross-layer scenario exercises the durable chain:

`DOS_STATIC Calculation -> DOSCAR + atom-index-map Artifacts -> DOS Analysis -> canonical DOS Artifact -> BAND_CENTER Analysis -> descriptor Artifact -> ProjectStore reopen -> electronic-analysis reconciliation`.

The scenario then injects an upstream canonical-DOS hash drift and requires the downstream descriptor reconciliation to become stale/blocked through the existing SCIENTIFIC dependency DAG.

### 2. Final DOS/descriptor E2E invariants

`tests/test_v07_e2e_acceptance.py` locks the following combined invariants:

- DOSCAR parsing binds projected site data to the permanent `atom_uid` from the exact frozen atom-index map;
- canonical DOS preserves the native VASP energy axis and Fermi energy and exposes Fermi-relative energies only through the explicit transformation;
- durable DOS source receipts retain the exact DOSCAR SHA-256, atom-index-map SHA-256, parser version, and parameters hash;
- the canonical DOS Artifact reopens without changing numerical facts;
- a BAND_CENTER Analysis consumes the exact canonical DOS Artifact and reopens to the same result;
- ProjectStore save/open preserves the complete Calculation/Artifact/Analysis/provenance/dependency chain;
- repeated electronic-analysis reconciliation over unchanged persisted state is deterministic and hash-identical;
- upstream canonical-DOS hash drift propagates to the downstream BAND_CENTER requirement as STALE/BLOCKED.

### 3. Descriptor enum hardening

Final review found one runtime fail-closed gap in the descriptor constructor surface. Python type annotations do not prevent callers from supplying arbitrary strings through dynamic/runtime code. Before Block 9, a forged `BandCenterKind`-like string could reach descriptor selection logic and potentially bypass the intended p/d angular-momentum branch.

Block 9 therefore requires explicit runtime validation for:

- `BandCenterSpinMode`;
- `BandCenterKind`;
- `BandCenterEnergyReference`;
- `BandCenterIntegrationRule`;
- `BandCenterNormalization`.

Unsupported values fail closed with `BandCenterError`; they are never reinterpreted as another descriptor family or numerical convention.

v0.7 intentionally supports one numerical integration rule (`TRAPEZOID_LINEAR_ENDPOINTS`) and one normalization convention (`DOS_WEIGHTED_FIRST_MOMENT`). A second rule is not added merely to manufacture an identity comparison. The identity payload includes the rule and normalization fields, while unsupported alternatives are rejected rather than silently evaluated under the supported rule.

### 4. ADR-048 acceptance matrix

The final acceptance criteria are covered as follows:

| ADR-048 criterion | Acceptance evidence |
| --- | --- |
| Verified DOS_STATIC -> canonical total DOS + permanent-atom PDOS | `tests/test_v07_doscar_intake.py`, `tests/test_v07_dos_materialization.py`, `tests/test_v07_e2e_acceptance.py` |
| Native energies/Fermi level survive serialize/reopen; no implicit zero shift | `tests/test_v07_dos_materialization.py`, `tests/test_v07_e2e_acceptance.py` |
| Analysis provenance records exact input Artifact hashes, parameters, tool version | `tests/test_v07_dos_materialization.py`, `tests/test_v07_bader_analysis.py`, `tests/test_v07_lobster_cohp.py`, `tests/test_v07_e2e_acceptance.py` |
| Scientific input/artifact drift makes electronic results/descriptors stale | `tests/test_v07_dos_materialization.py`, `tests/test_v07_electronic_descriptors.py`, `tests/test_v07_electronic_analysis_reconciliation.py`, `tests/test_v07_e2e_acceptance.py` |
| Bader intake reproducible from exact charge inputs + external-tool provenance | `tests/test_v07_bader_analysis.py` |
| Charge difference rejects incompatible structures/grids and records all three parents | `tests/test_v07_charge_difference.py` |
| LOBSTER cannot bind wrong prerequisite; COHP/ICOHP native facts remain untransformed | `tests/test_v07_lobster_cohp.py` |
| Band-center identity is parameterized by selector/spin/orbital/window/alignment/rule/upstream DOS | `tests/test_v07_electronic_descriptors.py`, `tests/test_v07_e2e_acceptance.py`; unsupported rule/normalization alternatives fail closed |
| ProjectStore reopen preserves full analysis/provenance chain | `tests/test_v07_dos_materialization.py`, `tests/test_v07_electronic_descriptors.py`, `tests/test_v07_electronic_analysis_reconciliation.py`, `tests/test_v07_e2e_acceptance.py` |
| v0.6 workflow acceptance remains valid; reconciliation does not mutate workflow history | `tests/test_workflow_v06_acceptance.py`, `tests/test_v07_electronic_analysis_reconciliation.py` |
| Ruff, mypy strict, pytest 3.11/3.12/3.13, MatterViz remain green | exact-head PR CI and post-merge main push CI |

### 5. Final v0.7 scope lock

Block 9 does not add:

- ZPE, entropy, thermal corrections, gas reference energies, CHE, reaction free-energy diagrams, potential/pH corrections, or any other v0.8 thermochemistry behavior;
- noncollinear/SOC projected DOS semantics;
- vacuum-level/work-function alignment;
- a new scheduler backend;
- GUI work;
- a second workflow/reconciliation persistence model;
- a new runtime dependency.

`SCHEMA_VERSION` remains 3. The package remains `0.7.0.dev0`. No tag, GitHub Release, or PyPI publication is created by routine completion of v0.7.

### 6. Completion rule

Block 9 is accepted only after:

1. the Block 9 PR exact head passes Ruff, mypy strict, pytest, Python 3.11/3.12/3.13, and MatterViz;
2. architecture/scientific self-review finds no blocking scope or provenance issue;
3. the PR is squash-merged using the exact-head guard; and
4. the resulting `main` push CI completes successfully on the merge SHA.

Only then is v0.7 considered stable and complete.

## Consequences

v0.7 closes with a test-backed scientific contract rather than another stateful subsystem. The final E2E path demonstrates that electronic facts, derived descriptors, durable storage, provenance freshness, and reconciliation compose correctly across module boundaries while preserving the frozen v0.1–v0.6 architecture and keeping thermochemistry isolated for v0.8.
