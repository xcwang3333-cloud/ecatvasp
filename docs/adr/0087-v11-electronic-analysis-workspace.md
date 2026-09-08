# ADR-087: v1.1 Electronic Analysis Workspace

- Status: Accepted
- Date: 2026-09-08
- Scope: v1.1 Block 6 — Electronic Analysis Workspace

## Context

ADR-048 and ADR-056 froze the v0.7 electronic-analysis scientific contracts. The repository already
contains canonical DOS/PDOS parsing and materialization, permanent-`atom_uid` projected identity,
Bader ACF intake/materialization, charge-density-difference materialization, LOBSTER COHP/ICOHP
intake/materialization, parameterized band-center descriptors, and electronic-analysis
reconciliation/freshness on the existing provenance DAG.

ADR-086 defines v1.1 as productization of existing scientific authorities through a typed desktop
workflow while keeping `SCHEMA_VERSION = 3`, `ProjectStore` as the durable authority, frozen desktop
IPC v1 compatibility, and Python as the only scientific authority.

Block 6 therefore does not need a second electronic-analysis engine. It needs a task-oriented
application/read boundary that can resolve current managed scientific evidence and present canonical
facts without making the frontend reconstruct provenance, atom identity, freshness, or scientific
interpretation.

The main scope risk is external-tool execution. v0.7 deliberately modeled Bader and LOBSTER as
external Analyses and implemented exact result intake/materialization, not a generic scheduler or
external-command framework. Block 6 must not silently widen result presentation into arbitrary local
or remote executable automation.

## Decision

### 1. Existing v0.7 analysis contracts remain authoritative

Block 6 reuses the existing canonical authorities for:

- DOS/PDOS: `parse_vasp_doscar`, `materialize_canonical_dos_analysis`,
  `load_canonical_dos_artifact`;
- Bader: `parse_bader_acf`, `materialize_bader_analysis`,
  `load_canonical_bader_artifact`;
- charge difference: `materialize_charge_difference_analysis`,
  `load_charge_difference_artifacts`;
- COHP/ICOHP: `parse_lobster_cohp`, `materialize_lobster_cohp_analysis`,
  `load_canonical_cohp_artifact`;
- band-center descriptors: `materialize_band_center_analysis`,
  `load_band_center_artifact`;
- freshness/readiness: the existing scientific dependency DAG and electronic-analysis
  reconciliation model.

No parser, convergence rule, atom mapper, freshness engine, or descriptor calculation is duplicated in
the desktop or in a new Block 6 scientific core.

### 2. Block 6 introduces a project-scoped application workspace, not a durable entity

A transient `ProjectElectronicAnalysisApplicationService` may reopen `ProjectStore`, resolve exact
current Calculations/ExecutionAttempts/Artifacts/Analyses, invoke existing v0.7 authorities, persist
existing `Analysis`/`Artifact`/provenance/dependency objects, and return display-ready facts.

It does not introduce `ElectronicWorkspace`, `AnalysisTask`, `Plot`, `Selection`, `Study`, `Session`,
or any other persisted top-level entity. `SCHEMA_VERSION` remains 3.

### 3. Typed desktop IPC v2 operations

Block 6 extends `ecatvasp-desktop-ipc-v2` with operation-specific requests only. The intended surface
is:

- `electronic_analysis_catalog` — page-scoped inventory/readiness/freshness projection;
- `materialize_dos_analysis` — create/reuse canonical DOS/PDOS from the exact managed DOS source;
- `electronic_analysis_view` — reopen one existing canonical electronic Analysis and return typed
  plot/table facts;
- `materialize_band_center` — create/reuse a fully parameterized band/p/d-band descriptor from one
  exact canonical DOS source.

No generic `analysis_command`, arbitrary `payload`, path, shell command, user-supplied artifact hash,
or user-supplied scientific verdict is accepted.

### 4. DOS materialization is server-side evidence resolution

`materialize_dos_analysis` accepts a `CalculationId`, not paths or hashes. Python must resolve:

- the exact `DOS_STATIC` Calculation;
- its exact MethodFingerprint and input StructureSnapshot;
- the latest managed ExecutionAttempt;
- one exact local/retrieved DOSCAR produced by that attempt;
- one exact frozen `atom-index-map.json` Artifact belonging to that Calculation;
- the fingerprint spin treatment.

Missing, duplicate, foreign, stale, unavailable, or hash-drifted inputs fail closed. The canonical
v0.7 parser/materializer remains responsible for scientific validation and atom-UID binding.

Repeated materialization of the same exact scientific identity must reuse the existing durable
Analysis rather than append a duplicate.

### 5. Bader, charge difference, and COHP/ICOHP are first-class presentation targets

`electronic_analysis_view` must support already-durable `DOS`, `BADER`, `CHARGE_DIFFERENCE`, `COHP`,
and `BAND_CENTER` Analyses.

The view loader must resolve analysis-produced Artifacts by producer identity and canonical filenames /
contract roles, then call the existing v0.7 reopen validators. It must never deserialize an arbitrary
file path supplied by the frontend.

This makes imported/materialized Bader, charge-difference, and LOBSTER results fully usable in the
Block 6 desktop workspace even when their external executables were run outside ECatVASP.

### 6. External Bader/LOBSTER executable automation is not added in Block 6

Block 6 does not add:

- arbitrary subprocess execution;
- arbitrary shell/argv desktop RPC;
- a second remote scheduler/execution model for analysis tools;
- automatic installation or discovery of Bader/LOBSTER executables;
- licensed-data transport or POTCAR-body persistence.

A later narrowly scoped executable integration may be considered only if it can reuse existing
execution architecture without becoming a generic external-command platform. That is not required for
Block 6 acceptance.

### 7. Canonical scientific facts and display transforms remain separate

The backend returns canonical facts plus explicit presentation metadata.

For DOS/PDOS:

- canonical energies remain native VASP energies;
- `E - E_F` may be returned as an explicit display axis alongside the native axis;
- spin channels remain explicit;
- atom selection uses permanent `atom_uid`;
- orbital labels remain canonical parser labels.

For LOBSTER:

- canonical COHP values retain the native LOBSTER sign;
- `-COHP` may be returned as an explicit display transform;
- native ICOHP values remain unmodified facts;
- pair identities remain permanent-atom identities when available.

The frontend may choose which supplied series to display and perform non-scientific visual transforms
such as hiding, ordering, scaling, mirroring spin-down for conventional plots, or rendering `-COHP`.
It may not recalculate scientific descriptors, infer atom identity, or rewrite canonical source facts.

### 8. Charge-difference large data remains file-backed

Block 6 does not embed volumetric density arrays into ProjectStore or ordinary catalog payloads.

The default charge-difference view returns validated metadata: grid shape, units, extrema, electron
integrals, convention, and exact analysis/artifact identity. Any later interactive volumetric transfer
must be an explicit bounded read path; large-data presentation alone does not justify schema v4.

### 9. Band-center parameters are explicit scientific identity

`materialize_band_center` accepts explicit typed parameters matching the v0.7 contract:

- descriptor kind (`band`, `p_band`, `d_band`);
- projection scope;
- spin mode;
- optional permanent `atom_uid` / element selector as required by scope;
- energy reference (`vasp_native` or `fermi_relative`);
- lower and upper integration bounds.

Integration rule and normalization remain the single frozen v0.7 supported conventions and are not
frontend-selectable aliases. Unsupported values fail closed in the existing descriptor constructors.

Repeated requests with the same exact source and parameters reuse the existing durable descriptor;
changed source or parameters create a distinct Analysis identity.

### 10. Freshness is computed in Python

Catalog/view responses include current freshness or a fail-closed readiness reason derived through the
existing provenance/freshness authorities. The frontend does not infer freshness from timestamps,
filenames, Calculation status, or whether a file exists.

### 11. Desktop workspace

The production Svelte/Tauri application adds an Analysis workspace that can:

- list electronic sources and durable analyses;
- materialize canonical DOS when exact managed inputs are ready;
- inspect actual DOS/PDOS curves with native and Fermi-relative axes;
- select spin, atom UID, element, and orbital series for presentation;
- inspect Bader per-site electron populations and basin values;
- inspect charge-difference metadata without loading the full volume by default;
- inspect COHP/ICOHP pair/orbital series and explicit native/`-COHP` display values;
- create and inspect band-center descriptors.

Raw UUIDs/hashes remain available under advanced/identity disclosure but are not normal manual inputs.

### 12. Frozen boundaries

Block 6 does not change:

- desktop IPC v1 operation semantics;
- `SCHEMA_VERSION = 3`;
- `ProjectStore` authority;
- Calculation/ExecutionAttempt/RemoteJob separation;
- v0.7 canonical electronic-analysis semantics;
- scientific convergence semantics;
- atom identity rules;
- thermochemistry/reaction identity;
- package release state.

It also does not add noncollinear/SOC projected-DOS semantics, vacuum/work-function alignment, band
structure, Wannier analysis, ELF interpretation, COOP/COBI, constant-potential/grand-canonical DFT,
NEB/TS, microkinetics, or Study/sweep/ranking semantics.

## Acceptance

Block 6 is accepted only when tests demonstrate:

1. strict operation-specific IPC decoding with unknown-field rejection and no generic payload/path/hash
   escape hatch;
2. exact current managed DOS source resolution and canonical DOS materialization/reuse;
3. permanent-`atom_uid` PDOS survives the desktop application/view path;
4. native VASP energy and explicit Fermi-relative display axes remain distinguishable;
5. existing Bader, charge-difference, COHP/ICOHP, and band-center artifacts reopen through canonical
   validators and produce typed presentation payloads;
6. LOBSTER native COHP sign and explicit `-COHP` display transform remain distinguishable;
7. band-center identity changes with source or scientific parameters and exact duplicates are reused;
8. upstream artifact drift/staleness is surfaced by Python rather than inferred in the frontend;
9. project switching cannot allow stale asynchronous Analysis responses to overwrite the active
   project;
10. IPC v1 and schema3 remain frozen and no Study/schema4/external-command framework is introduced;
11. Ruff, strict mypy, pytest on Python 3.11/3.12/3.13, MatterViz, desktop typecheck/tests/build,
    Tauri transport guards, Windows frozen-backend E2E, NSIS packaging/output verification, and
    installer upload all pass on the exact PR head;
12. anchored self-review, final merge guard, expected-head squash merge, and exact-main post-merge CI
    all pass before Block 6 is frozen.

## Consequences

Block 6 turns the already-complete v0.7 electronic scientific layer into an operable desktop research
workspace without adding a second scientific engine or a generic external-tool workflow. The product
can present real DOS/PDOS, Bader, charge-difference, COHP/ICOHP, and descriptor results while retaining
ProjectStore/provenance/atom-identity authority and leaving executable orchestration as a separate,
explicit future decision.