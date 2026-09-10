# ADR-091: v1.2 Architecture Baseline and Dependency Audit

- Status: Accepted
- Date: 2026-09-10
- Scope: v1.2 Block 0 — Architecture Baseline & Dependency Audit
- Roadmap authority: Issue #109
- Audited baseline: `main@1b1f6f2768c55087c7d4c417dad30b29c77eeb46`

## Context

ECatVASP v1.1 is frozen as an accepted development baseline, not a public release. The audited
baseline remains:

- Python package `1.1.0.dev0`;
- desktop/Tauri package `1.1.0-dev.0`;
- `SCHEMA_VERSION = 3`;
- Development Status `Alpha`;
- installed Windows desktop scientific E2E accepted;
- no `v1.1.0` tag, GitHub Release, or PyPI publication;
- real institutional OpenSSH/Slurm/VASP/licensed-POTCAR execution remains `PROTOCOL_DEFINED`, not
  `EXECUTED_VERIFIED`.

Issue #106 closed the v1.1 release-readiness/governance audit and named Issue #109 as the successor
planning authority. Issue #109 deliberately prioritizes real research operations before advanced
scientific expansion.

Block 0 therefore asks one architecture question before any v1.2 feature implementation begins:

> Which v1.2 goals fit the existing schema-3 authority model, and which goals genuinely require a new
> architecture decision or durable scientific semantics?

This audit read the repository root, `pyproject.toml`, Python package layout, `ui/desktop`, Tauri/Rust,
CI, ProjectStore/schema/migrations, IPC v1/v2, structure/model/calculation/workflow domains,
execution/HPC/recovery/batch, VASP input/result/convergence, electronic analysis,
thermochemistry/CHE/reaction, provenance/freshness, ADR index and ADR-086 through ADR-090, the v1.1
closure record, the real-HPC acceptance protocol, dependency manifests, and the historical
`plan/v11-study-screening-architecture` Study proposal.

## Decision summary

1. **Schema 3 remains the v1.2 baseline.** Real-site operations, existing-method workflow UX,
   bounded batch execution, richer adsorbate construction, and current reaction-family productivity
   do not by themselves require schema 4.
2. **The current authority chain remains unique.** No second scientific engine, persistence authority,
   convergence engine, workflow state machine, or frontend scientific authority is introduced.
3. **Real Site Preflight is the first v1.2 implementation priority.** It is an execution/application
   capability over the existing `ExecutionTargetProfile`, OpenSSH, Slurm, POTCAR, and execution
   boundaries; it is not a new scientific entity.
4. **Study is `ARCHITECTURE_RESEARCH_ONLY` for v1.2 at this gate.** The historical schema-4 Study
   proposal remains useful design input but is not implementation authority.
5. **Dependency restructuring is not required before Block 1.** The current dependency surface is
   intentionally small. Reproducible-install/lockfile policy is a documented governance debt, not a
   reason to modernize dependencies opportunistically in Block 0.

## Current authority map

### Durable authority

`ProjectStore` is the only durable project authority. Schema-3 `ProjectBundle` persists the current
scientific and execution graph:

- `Project`;
- `Catalyst`;
- `StructureVariant`;
- `StructureSnapshot`;
- `ActiveSite`;
- `AdsorptionState`;
- `StateConformer`;
- `MethodFingerprint`;
- `ScientificWorkflowPlan`;
- `WorkflowStepBinding`;
- `Calculation`;
- `ExecutionAttempt`;
- `RemoteJob`;
- `Artifact`;
- `Analysis`;
- `ProvenanceRecord`;
- `DependencyRecord`.

Unsupported persisted top-level entity types are rejected. A capability that truly requires a new
permanent entity family must stop for a separate ADR and migration decision.

### Scientific authority

Python domain/services remain the only authority for scientific identity, structure lineage,
MethodFingerprint construction, VASP recipes/input validation, result parsing, scientific convergence,
electronic analysis, thermochemistry, CHE/reaction evaluation, provenance, and freshness.

Third-party libraries may parse formats or provide numerical primitives, but they do not replace these
ECatVASP contracts.

### Desktop authority

Tauri/Svelte/TypeScript is a typed request, lifecycle, and presentation layer. Desktop-local preference,
request-generation, navigation, cache, filter, selection, and diagnostic state is non-scientific and
non-authoritative.

IPC v1 remains frozen. IPC v2 remains operation-specific and fail closed. v1.2 must not introduce a
generic scientific mutation, arbitrary shell/payload/path/hash/verdict RPC, or frontend-owned scientific
fallback.

### Execution namespaces

The invariant remains:

`Calculation != ExecutionAttempt != RemoteJob`.

`ExecutionTargetProfile` is execution-local configuration, not a scientific entity and not a
`ProjectBundle` member. Host-specific execution configuration may change without changing Calculation
scientific identity. A sanitized `ExecutionEnvironmentSnapshot` may be persisted with attempt
provenance.

### Scientific completion

Scheduler completion remains execution evidence only:

`scheduler completed != scientific convergence`.

Scientific convergence is a separate Python, recipe-aware verdict over exact retrieved managed VASP
evidence. No v1.2 preflight, site profile, scheduler status, or desktop state may collapse this boundary.

### Identity, provenance, and freshness

The existing authority is sufficient for v1.2 Layers 1 and 2:

- permanent atom identity is carried by `atom_uid` rather than reconstructed geometrically;
- Artifact identity is content-addressed through exact bytes/size/SHA-256 at managed boundaries;
- Analysis identity is parameter/source sensitive and remains Python-owned;
- scientific hashes exclude display/lifecycle metadata;
- `ProvenanceRecord` records production tool/version/parameters/method identity;
- `DependencyRecord` records exact upstream hashes and dependency kind;
- the dependency graph must remain a DAG;
- only `SCIENTIFIC` dependencies propagate scientific invalidation;
- stale/invalid/superseded states are derived in Python and fail closed at downstream scientific gates.

A site diagnostic timestamp, scheduler observation, or UI collection must never masquerade as a
scientific source hash.

## Dependency audit

### Python runtime

`pyproject.toml` declares only:

- `ase>=3.29,<4`;
- `numpy>=1.26`.

There is no runtime dependency on pymatgen, Pydantic, SciPy, Paramiko, or a Python Slurm client.

#### ASE

ASE is a **scientific convenience/interoperability dependency**, not ECatVASP durable or scientific
authority. Current usage includes structure file I/O/conversion and VASP charge-density handling. Atom
identity, domain validation, Calculation/Method identity, provenance, convergence, and result semantics
remain ECatVASP-owned.

Adding pymatgen merely to duplicate existing parsing/input authority is rejected unless a later ADR
identifies a concrete missing contract and a single canonical source policy.

#### NumPy

NumPy is a **numerical primitive dependency** used by scientific code. It is not an identity,
provenance, workflow, or persistence authority.

#### SSH / scheduler / VASP

Real SSH uses the user's **system OpenSSH** (`ssh`/`scp`) with BatchMode and strict host-key checking.
Credentials, private keys, passwords, tokens, host-routing policy, and MFA remain outside ProjectStore and
outside ECatVASP credential storage.

Slurm and VASP are external runtime/site capabilities reached through ECatVASP adapters/contracts, not
Python package dependencies. Their availability must be established by Real Site Preflight rather than
assumed from configuration text.

#### POTCAR

Licensed POTCAR bytes remain site-local. ECatVASP persists/specifies logical resolver identity,
requested POTCAR identity/order/hash evidence, and sanitized provenance; it must not package or persist
licensed POTCAR bodies.

#### LOBSTER / Bader

Current Bader and LOBSTER architecture is canonical **result intake/materialization**, not a second
general scheduler. Their executables are optional site tools, not core package dependencies. Real Site
Preflight may report their availability/version, but absence is not a VASP-core site failure unless the
requested workflow explicitly requires them.

#### Development / packaging

Development dependencies are ranged (`mypy>=1.11`, `pytest>=8.3`, `ruff>=0.8`). Windows packaging tools
are explicitly pinned in `ui/desktop/packaging/requirements-windows.txt`.

### Desktop / frontend / Rust

The desktop manifest currently pins direct JavaScript dependencies to exact versions, including Svelte,
MatterViz, Tauri API, Vite, TypeScript, and Vitest. MatterViz is a **presentation/visualization
convenience dependency** and cannot own atom identity or scientific interpretation. Svelte/TypeScript are
presentation/contract tooling. Tauri is the local desktop transport/lifecycle shell.

The Rust crate pins Tauri/Tauri-build exactly but leaves `serde_json` and `sha2` on compatible ranges.
Rust transport hashing/JSON handling does not replace Python scientific hashes or ProjectStore authority.

### Reproducibility risk

The repository currently has no committed npm lockfile or Cargo lockfile in the audited tree, CI uses
`npm install`, Rust `stable`, ranged Python runtime/development dependencies, and major-version GitHub
Actions references. Direct frontend versions are narrow, but transitive dependency resolution can still
drift between exact-head reruns.

This is a **dependency-governance/reproducibility risk**, not evidence of duplicate scientific authority.
Block 0 does not add lockfiles or upgrade packages because doing so would mix a dependency-policy change
into an architecture audit. A separate bounded dependency-reproducibility change may be scheduled when
there is repository evidence or before a public release. It must preserve all existing scientific and
packaging acceptance tests.

### Dependency conclusion

- No dependency currently acts as a competing durable authority.
- No duplicate scientific authority was found.
- The main coupling risk is product code passing raw execution-target dictionaries repeatedly through
  typed desktop actions; Block 1 should replace repeated user entry with a bounded typed site-profile
  application contract, not a new scientific entity.
- The main version risk is transitive build reproducibility, not an obviously obsolete core library.
- **No dependency restructuring is required for v1.2 Block 1.**

## Schema-3 capacity

Schema 3 has substantial remaining capacity because most near-term v1.2 goals are operations or
projections over existing durable entities.

Schema 3 directly supports:

- execution targets as non-domain configuration plus sanitized attempt provenance;
- SSH/Slurm staging/submission/monitor/retrieval;
- retry/recovery with explicit identity-boundary classification;
- scheduler-only batch DAGs over already-materialized Calculations/ExecutionPlans;
- additional typed application operations and page-scoped projections;
- richer structure/adsorbate constructors that still produce existing StructureSnapshot/
  AdsorptionState/StateConformer entities;
- additional recipes/workflow conveniences that resolve to existing MethodFingerprint, Calculation,
  ScientificWorkflowPlan, and WorkflowStepBinding identities;
- existing HER/ORR/OER/CO2-to-CO thermochemistry/reaction UX;
- transient collections, filters, comparisons, and previews whose scientific sources remain exact
  existing entities.

Schema 3 is under structural pressure only when a capability needs durable semantics that cannot be
faithfully represented by the existing entity graph, especially:

- a cross-candidate scientific design with permanent membership identity;
- durable sweep-axis/design identity;
- durable ranking/selection policy and its own provenance-bearing identity;
- a new scientific model whose parameters/conditions cannot be represented by existing
  MethodFingerprint/Analysis/Artifact/Reaction contracts without ambiguous overloading;
- a new persistent entity participating in provenance/freshness as a first-class scientific source.

Performance, pagination, batch size, UI complexity, or the number of Calculations are not schema-4
arguments by themselves.

## v1.2 capability classification

The classification scale is:

- **A** — schema3 and existing architecture already provide the required semantic authority;
- **B** — schema3 is sufficient, but a new typed operation/service/UI contract is required;
- **C** — a separate ADR is required before implementation, but schema4 is not assumed;
- **D** — the capability probably requires schema4, a new permanent entity, or new durable identity
  semantics.

| Capability | Class | Architecture finding |
| --- | --- | --- |
| reusable server/site profiles | **B** | `ExecutionTargetProfile` semantics already exist and are non-scientific; product reuse needs a bounded desktop-local/application profile contract, not ProjectStore persistence |
| SSH/Slurm preflight | **B** | existing OpenSSH/Slurm adapters are sufficient primitives; needs one typed preflight service/result contract before any attempt is created |
| VASP environment detection | **B** | detect executable/module/MPI environment through a safe typed probe; no new scientific entity |
| POTCAR mapping | **A/B** | exact resolver/family/order/hash semantics already exist (**A**); reusable site validation/configuration needs typed preflight/profile operations (**B**) |
| execution recovery | **A** | recovery identity classification and attempt boundaries already exist; v1.2 should harden real failure evidence rather than redesign semantics |
| richer adsorbate workflows | **B** | use existing structure/ActiveSite/AdsorptionState/StateConformer identities through additional typed constructors/workflow operations |
| calculation recipes | **B** | registry/fingerprint/workflow authorities exist; new supported recipe/product flows require explicit typed recipe/service contracts, not generic INCAR mutation |
| ORR/OER/HER/CO2RR workflow UX | **B** | existing thermochemistry/CHE/reaction authorities already cover current supported mechanisms; product orchestration/presentation may expand without new durable entities |
| bounded batch calculations | **A/B** | scheduler-only batch DAG/recovery semantics already exist (**A**); desktop batch actions/projections are **B** and must not become a Study surrogate |
| transient sweep preview/collection | **B** | may expand explicit typed variants into existing identities and remain non-persisted |
| durable Study | **D** | multi-root permanent membership/design identity is absent from ProjectBundle and would require a new persisted entity family |
| durable sweep design | **D** | exact sweep axes/resolved assignments as durable scientific design require new identity/provenance semantics |
| transient ranking/sort for inspection | **B** | allowed only as clearly non-scientific presentation over exact Python-supplied facts |
| durable scientific ranking/selection | **D** | criterion/comparability/missing-data/tie/selection policy needs provenance-bearing durable identity if it drives downstream science |
| NEB / transition-state workflow | **C** | needs a separate ADR for path/image/barrier identity and workflow semantics; schema4 is not automatic because existing Calculation/Workflow/Analysis may be sufficient |
| constant-potential / grand-canonical DFT | **C** | new physical/method identity semantics require an ADR; migration is decided only after mapping those semantics against MethodFingerprint/Calculation contracts |
| microkinetics | **C** | new kinetic model, rate/condition identity, and provenance require an ADR; a typed Analysis-based design may remain schema3 if no permanent new entity is necessary |
| full Pourbaix | **C** | new phase/reference/condition semantics require a scientific ADR; a derived Analysis/dataset may remain schema3 if existing provenance is sufficient |
| ML/surrogate screening | **C** | requires an ADR for model provenance, comparability, proposal vs scientific-result boundaries; becomes **D** only if a durable campaign/model entity is justified |

For rows marked `A/B`, the existing semantic core is already class A; only the user-facing reusable
operation is class B.

## Study / schema4 decision

Decision for v1.2 Block 0:

**`ARCHITECTURE_RESEARCH_ONLY`**

### Current real user need

Issue #109 establishes the current unmet need as real research operations on an institutional site and
then productivity across the existing electrocatalysis workflow. The repository already has scheduler
batch primitives and large-project handling. There is no new v1.2 evidence at this gate showing that a
permanent Study entity is required before real-site execution is usable.

### Durable identity and grouping semantics

The historical `plan/v11-study-screening-architecture` proposal correctly identifies the point at which
a Study becomes real science rather than UI grouping: exact multi-root membership, typed design axes,
resolved assignments, comparability policies, durable ranking/selection, source hashes, and scientific
dependencies.

Those semantics are not representable as a harmless saved table filter. If v1.2 later needs them for
reproducible downstream decisions, they should receive a separate ADR and likely schema4.

### What can remain transient

Most immediate productivity needs can use schema3 without a Study:

- transient collections of StructureSnapshots/Calculations;
- page filters/search/sort;
- typed sweep preview that materializes ordinary existing scientific identities;
- bounded scheduler batch dispatch over existing Calculations;
- comparison/ranking displays that are explicitly presentation-only and do not determine downstream
  scientific work automatically.

### Migration cost

The historical Study proposal requires codec/ProjectBundle extension, a v3->v4 migration, new scientific
hashing/provenance/freshness tests, desktop contracts, migration E2E, ranking/selection semantics, and
large-project acceptance. That cost is justified only by a demonstrated durable research-design need,
not by a desire to organize many rows.

`ARCHITECTURE_RESEARCH_ONLY` permits requirements gathering and isolated design comparison during
v1.2, but no schema4 migration or Study implementation may begin from this ADR.

## Real Site Preflight architecture

Block 1 should build **Real Site Preflight** over the existing execution architecture.

### Profile boundary

A reusable site profile is user-local operational configuration derived from the existing
`ExecutionTargetProfile` contract. It may contain only safe operational values such as:

- stable profile/target id;
- OpenSSH host alias;
- remote work root;
- scheduler family;
- ordered module identifiers;
- VASP executable command name;
- MPI launcher command name;
- logical POTCAR resolver id/family/source mapping reference;
- optional expected Bader/LOBSTER tool names/capabilities.

It is **not** a ProjectStore scientific entity. Credentials, passwords, private-key bodies,
passphrases, tokens, raw SSH options, arbitrary shell fragments, and licensed POTCAR bodies remain
unrepresentable.

### Preflight operation

The preflight must be one operation-specific Python application/execution service with a typed desktop
request/response. It runs before any scientific submission and must not create a `Calculation`,
`ExecutionAttempt`, or `RemoteJob` merely to test a site.

The service should evaluate explicit gates, at minimum:

1. local system OpenSSH client availability and frozen BatchMode/strict-host-key policy;
2. non-interactive SSH reachability through the configured host alias;
3. configured remote work root containment, existence/creation policy, and write/read diagnostic access;
4. Slurm readiness (`sbatch`, `squeue`, `sacct`, `scancel`) and scheduler family consistency;
5. safe module/environment activation using generated typed script content or equivalent bounded adapter
   semantics — never arbitrary frontend shell text;
6. configured VASP executable resolution and a bounded version/identity probe;
7. MPI launcher resolution and compatibility evidence where the target declares one;
8. POTCAR resolver/family/root readiness and exact requested-title/order/digest validation using site-local
   licensed material only;
9. optional LOBSTER/Bader availability/version diagnostics, reported independently from core VASP
   readiness unless a selected workflow requires them.

### Diagnostic result

Preflight returns structured gate results such as `READY`, `BLOCKED`, `WARNING`, or
`UNAVAILABLE_OPTIONAL`, with stable machine-readable codes, sanitized evidence, observation time, and
profile/target hash.

The result is operational evidence, not scientific convergence or a scientific freshness source. A
cached/displayed prior preflight must be visibly stale after relevant profile changes and must never be
accepted as proof that a later SSH/Slurm operation still succeeds.

When a real execution attempt is created, existing immutable attempt/environment/plan provenance remains
the authority. Preflight cannot overwrite or substitute that evidence.

### Real-site acceptance

Block 1 must distinguish:

- deterministic CI coverage of the preflight service/contracts;
- actual site-specific evidence recorded under `docs/acceptance/real-hpc-vasp.md` statuses.

If no institutional target is available, CI can accept the product implementation but the corresponding
real-site gates remain `PROTOCOL_DEFINED`; they must not be relabeled `EXECUTED_VERIFIED`.

## v1.2 block sequence

### Block 0 — Architecture Baseline & Dependency Audit

Freeze this ADR, schema3 capacity, dependency findings, capability classes, Study decision, Real Site
Preflight boundary, and roadmap order.

### Block 1 — Real Site Profile & Preflight

Implement reusable non-scientific site-profile UX/application contracts and typed Real Site Preflight.
Provide deterministic CI evidence and execute the Audit-E protocol on an institutional target when one is
actually available.

Depends on Block 0 because profile persistence/security and diagnostic authority must be fixed before UI
or probes are added.

### Block 2 — Real HPC Reliability & Recovery

Exercise and harden staging, submission, monitoring, cancellation, retrieval, incomplete retrieval,
lost/unknown scheduler state, reconnect/restart, retry/resubmit/new-attempt boundaries, remote cleanup,
and diagnostics against real-site evidence.

Depends on Block 1 because recovery evidence is meaningful only after target/environment readiness is
observable and profile identity is stable.

### Block 3 — Electrocatalysis Workflow UX Audit

Audit the real user path for adsorbate construction, recipe choice, workflow preparation, execution,
result/promotion, electronic analysis, thermochemistry, and HER/ORR/OER/CO2RR reactions. Produce a
bounded gap list classified against this ADR before adding scientific workflow features.

This block is intentionally an audit/gap-selection gate so v1.2 does not turn into uncontrolled feature
accumulation after HPC work.

### Block 4 — Scientific Workflow Productivity

Implement only Block-3-selected schema3 capabilities: richer adsorbate workflows, reusable calculation
recipe flows, current reaction-family UX, result-to-analysis handoff, and bounded batch productivity.
Every addition must resolve to existing scientific identities and typed operations.

### Block 5 — Advanced Physics Architecture Research

NEB, constant-potential/grand-canonical DFT, microkinetics, and full Pourbaix may receive separate ADRs
or research spikes only after Blocks 1-4 expose a concrete research need. No capability in this block is
implicitly approved for implementation by ADR-091.

### Block 6 — Study / Scale Architecture Research

Re-evaluate durable Study/sweep/ranking only using evidence from actual projects, batch usage, and
scientific comparison workflows. If durable membership/ranking/selection is demonstrably required, open a
separate Study ADR and decide schema4 there.

### Block 7+ — Scale & Automation

Only after identity/provenance contracts for the underlying work are stable should ECatVASP consider
large sweep orchestration, ranking/screening automation, ML-assisted screening, or broader cross-site
research automation.

## Stop conditions

Any v1.2 implementation Block must stop for a new architecture decision if it first requires:

1. a new permanent scientific entity or provenance-bearing durable grouping;
2. a new scientific identity dimension that cannot be expressed unambiguously by existing contracts;
3. `SCHEMA_VERSION = 4`;
4. persisted Study/sweep/ranking/selection semantics;
5. a second persistence, workflow, freshness, convergence, parser, or scientific state authority;
6. scheduler state or site-preflight success being interpreted as scientific convergence;
7. frontend calculation of scientific identity, provenance, freshness, comparability, ranking, or
   convergence;
8. generic scientific mutation, arbitrary JSON payload, arbitrary filesystem path/hash, arbitrary shell,
   or credential-bearing desktop RPC;
9. persistence/logging of passwords, private-key bodies, tokens, host-key bypasses, or licensed POTCAR
   bodies;
10. automatic recovery that changes scientific inputs without creating the required new scientific
    identity;
11. a new physical method whose conditions/parameters alter scientific identity without a dedicated ADR;
12. dependency restructuring or major-version upgrades without a concrete compatibility/security/
    reproducibility reason and full acceptance evidence.

## Acceptance criteria

v1.2 Block 0 is accepted only when:

1. the branch is based exactly on `1b1f6f2768c55087c7d4c417dad30b29c77eeb46` and no unreviewed main
   movement is incorporated;
2. Issue #109 remains the v1.2 roadmap authority and is reconciled to this ADR after merge;
3. `SCHEMA_VERSION` remains 3 and package/desktop versions remain in the v1.1 development state;
4. the ProjectStore/Python/desktop/execution/convergence/provenance authority map above is frozen;
5. dependency roles, duplication/coupling/version risks, and the no-restructuring decision are recorded;
6. every Issue-#109 candidate capability is classified A/B/C/D with schema pressure explicit;
7. Study is recorded as `ARCHITECTURE_RESEARCH_ONLY`, with no schema4 implementation implied;
8. Real Site Preflight architecture and its security/authority boundary are explicit;
9. the v1.2 Block sequence and stop conditions are explicit;
10. Block 0 changes only architecture/roadmap documentation unless a test is required to prove an existing
    assumption;
11. exact-head CI passes on the final PR head;
12. patch audit and self-review confirm no product/scientific/schema/dependency implementation leaked into
    the PR;
13. the PR is merged only from the expected unchanged head;
14. exact-main post-merge CI completes successfully before Block 0 is frozen.

## Consequences

v1.2 begins by making the already-designed workbench operate reliably on a real institutional research
site rather than paying schema4 and advanced-physics costs prematurely. Schema3 remains the default
until a new durable scientific identity is demonstrated, while Study and advanced physics remain explicit
architecture research topics instead of accidental scope. The dependency surface remains small and
scientific authority stays inside ECatVASP Python services, with third-party libraries and desktop
frameworks confined to their interoperability, numerical, transport, and presentation roles.
