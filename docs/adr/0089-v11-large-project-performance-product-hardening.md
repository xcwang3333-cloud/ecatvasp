# ADR-089: v1.1 Large-Project Performance & Product Hardening

- Status: Accepted
- Date: 2026-09-09
- Scope: v1.1 Block 8 — Large-Project Performance & Product Hardening

## Context

Blocks 1–7 have turned the existing ECatVASP scientific core into a closed-loop desktop workflow over
schema-3 `ProjectStore`. The current post-merge-CI-verified baseline is
`main@784c9e45dade94116e57e830df0a9a06a307c734`.

The scientific and durability boundaries are already frozen:

- `ProjectStore` is the sole durable project authority;
- Python domain/workflow/VASP/execution/analysis/thermochemistry code is the sole scientific authority;
- IPC v1 remains frozen and IPC v2 is typed and operation-specific;
- `SCHEMA_VERSION = 3` and package version remains `1.1.0.dev0`;
- no Study/Sweep/Ranking/session persistence or new identity-changing physics belongs in v1.1.

Block 8 is therefore not a new scientific feature block. Its purpose is to make the accumulated desktop
workflow behave predictably on large real projects and to remove product-level inefficiencies without
weakening scientific provenance or integrity checks.

The baseline audit identifies several concrete pressure points:

1. `ProjectStore.open()` verifies the SQLite file against the project manifest, reconstructs the complete
   domain graph, and validates persisted entity manifests. That work is intentionally authoritative and
   should not be skipped merely for speed.
2. Opening one desktop project currently mounts several data-heavy workspaces at once. Model Studio,
   Calculation Wizard, Job Center, Workspace inventory, Result Center, Electronic Analysis, and
   Thermochemistry/Reaction surfaces can each issue their own catalog/read request. Multiple independent
   requests can therefore repeat a full verified ProjectStore reopen even when the corresponding surface
   is not currently being used.
3. Individual application services often perform collection scans and, in some mutation paths, reopen
   the store more than once to defend against source drift. Those guards are scientifically valuable;
   optimization must distinguish necessary revalidation from avoidable duplicate work.
4. Long lists and scientific payloads can create frontend rendering pressure even when backend scientific
   computation is correct.
5. The generic TypeScript IPC-v2 operation catalog currently reflects Block 1–6 operations while Block 7
   feature contracts add seven more typed operations. Runtime compatibility is not broken because the
   health guard requires a minimum capability set, but the generic catalog should be synchronized.
6. The gas-reference form currently normalizes symmetry/spin with `Math.trunc`. Backend validation remains
   strict, but product hardening should reject fractional scientific input explicitly rather than silently
   normalize it.

## Decision

### 1. Scientific integrity and durability checks are not performance knobs

Block 8 must not make large projects faster by weakening or bypassing:

- project database SHA verification;
- ProjectStore manifest validation;
- canonical Artifact byte/hash/size validation at scientific boundaries;
- provenance or SCIENTIFIC dependency reconciliation;
- source-change guards before mutation persistence;
- atom-UID, MethodFingerprint, Analysis, Artifact, Calculation, ExecutionAttempt, or RemoteJob identity.

A performance optimization that changes those semantics is out of scope for Block 8 and requires a new
architecture decision.

### 2. Reduce unnecessary work before optimizing necessary work

The preferred optimization order is:

1. do not mount or fetch inactive desktop workspaces;
2. coalesce redundant refreshes after one mutation;
3. reuse one already-verified `ProjectBundle` within a single backend request where semantics permit;
4. build transient request-scoped lookup indices instead of repeatedly scanning large tuples;
5. bound or virtualize presentation work for long lists/large datasets;
6. consider cross-request caching only if earlier measures are insufficient and only under the cache
   contract below.

This order preserves simple authority boundaries and targets multiplicative work first.

### 3. Desktop workspaces become explicitly lazy

Large scientific workspaces must not all fetch catalogs merely because a project was opened.

The desktop may expose task/navigation selectors and mount only the active heavy workspace. Switching a
surface may fetch its current catalog at that moment. This is presentation state only and is never
persisted to ProjectStore.

Lazy mounting must preserve:

- project-switch A -> B -> A stale-response protection;
- same-project supersession guards;
- mutation receipt guards;
- explicit refresh behavior;
- accessibility and keyboard-reachable navigation.

The default active surface must be deterministic and must not infer scientific priority.

### 4. Request-scoped bundle reuse is allowed; cross-request authority is not

Within one typed backend operation, application helpers may share the same already-opened and verified
`ProjectBundle` instead of independently calling `ProjectStore.open()` again, provided that no durable
mutation has occurred between those reads.

Before a write that depends on previously resolved scientific inputs, the existing source-change guard
must remain. If current code deliberately reopens ProjectStore immediately before persistence to detect
concurrent drift, that reopen is retained unless an equivalent verified guard is proven.

A request-scoped bundle or index is transient and cannot be serialized as new durable state.

### 5. Transient indices may accelerate lookups without becoming scientific identity

A verified `ProjectBundle` may be accompanied by ephemeral maps such as:

- entity ID -> entity;
- Calculation ID -> related attempts/plans/analyses;
- Analysis ID -> produced artifacts;
- producer ID -> artifacts;
- dependency upstream/downstream adjacency;
- StructureSnapshot ID -> structure metadata.

Such indices must be deterministic derivations of the current bundle, disposable at any time, and never
accepted from the frontend. They do not change schema3 or scientific hashes.

### 6. Cross-request caches, if introduced, are strictly non-authoritative

Block 8 does not require a persistent cache. If measurement later shows a cross-request read-through cache
is necessary, it must:

- live only in process memory;
- be keyed to a verified ProjectStore generation/content identity rather than path alone;
- invalidate on every successful durable mutation and when the verified database/manifest identity
  changes;
- never satisfy a mutation source-change guard without current verification;
- never be written into ProjectStore or become a scientific/provenance source;
- be safe to discard on backend restart.

Introducing any durable cache/index table for performance is deferred unless separately justified.

### 7. Large-result presentation is bounded without truncating scientific meaning silently

The frontend may use lazy sections, list virtualization, pagination, collapsed details, sampled visual
rendering, or explicit user-selected series to control DOM and plotting cost.

However:

- scientific datasets remain complete in canonical artifacts;
- any paging/truncation must be explicit and expose totals/selection context;
- plot downsampling must be a presentation transform and must not alter exported/canonical numerical data;
- no freshness/readiness/scientific verdict may be inferred from whether a row is currently rendered.

### 8. Generic IPC-v2 capability metadata is synchronized

`ui/desktop/src/lib/backend/contracts-v2.ts` must include the complete frozen Block 7 operation vocabulary
in its generic operation catalog. Feature-specific contracts remain the typed authority for their request
fields and payloads; synchronization does not create a generic mutation request type.

Health compatibility continues to fail closed when required operations are absent.

### 9. Scientific form inputs fail closed rather than being silently normalized

For integer scientific inputs such as gas symmetry number and spin multiplicity, the desktop must reject
non-integer values before constructing a request. It must not silently floor, round, truncate, or otherwise
change the user's scientific input.

Python remains the final validation authority.

### 10. Performance claims require reproducible evidence

Block 8 will add deterministic large-project fixtures or benchmark-style regression tests that exercise a
representative schema-3 project graph. The objective is to detect algorithmic/request-fan-out regressions,
not to encode fragile wall-clock promises tied to one CI runner.

Preferred acceptance evidence includes:

- counted ProjectStore reopen calls for one user-visible action;
- counted backend catalog requests on initial project open and workspace switching;
- deterministic entity counts and payload sizes;
- lookup/iteration-count or structural tests for request-scoped indices;
- large-list frontend contract tests;
- existing full cross-platform CI and Windows packaging/E2E.

Wall-clock measurements may be recorded diagnostically but are not the sole merge gate unless a stable,
well-justified threshold is established from repository evidence.

### 11. Product hardening includes resilience and accessibility, not cosmetic redesign

Block 8 may improve:

- inactive/loading/empty/blocked/error states;
- navigation continuity and focus behavior;
- stale request/mutation handling;
- backend restart/recovery presentation;
- long-path/long-label overflow behavior;
- keyboard and accessible naming for workspace navigation;
- large-list rendering and scroll containment.

It does not perform an unrelated visual redesign or change scientific terminology without evidence.

### 12. Frozen boundaries remain unchanged

Block 8 does not add:

- schema4 or new durable entity families;
- Study/Sweep/Ranking/Task/Session/WorkspaceState persistence;
- a second workflow/freshness state machine;
- frontend scientific inference;
- generic payload/path/hash/shell/verdict RPCs;
- constant-potential/grand-canonical DFT, charged-cell CHE, new solvation physics, barriers/NEB,
  microkinetics, full Pourbaix, ML screening, or hidden empirical correction databases;
- tag, GitHub Release, or PyPI publication.

## Implementation sequence

Block 8 is organized as the following hardening sequence:

1. **Baseline & instrumentation** — large-project fixtures, request/reopen accounting, payload/fan-out
   characterization, and synchronization of the generic IPC-v2 operation catalog.
2. **Lazy task surfaces** — prevent inactive heavy workspaces from mounting/fetching on project open.
3. **Backend request efficiency** — remove avoidable duplicate ProjectStore reopens and repeated scans
   while retaining mutation-time source guards.
4. **Large-list/payload hardening** — explicit pagination/virtualization/lazy details where repository
   evidence shows pressure.
5. **Scientific-input & UX hardening** — fail-closed integer inputs, error/blocked/empty states,
   accessibility, overflow, navigation and recovery behavior.
6. **Large-project E2E acceptance** — exact-head CI plus Windows frozen-backend/desktop packaging and
   deterministic stress/regression coverage.

## Acceptance

Block 8 is accepted only when:

1. opening a project no longer causes every inactive heavy scientific workspace to fetch its catalog;
2. lazy workspace switching preserves all project/supersession/mutation stale-response guards;
3. request-scoped optimization does not bypass ProjectStore integrity, Artifact validation, provenance,
   freshness, or mutation source-change checks;
4. generic TypeScript IPC-v2 operation metadata is synchronized through Block 7 without adding a generic
   request escape hatch;
5. gas symmetry/spin fractional input is rejected explicitly rather than silently normalized;
6. deterministic large-project regression evidence demonstrates bounded request fan-out and guards
   against avoidable repeated full-store work;
7. schema3, IPC v1, Python scientific authority, package development state, and all deferred-physics
   boundaries remain frozen;
8. Ruff, strict mypy, pytest on Python 3.11/3.12/3.13, MatterViz, desktop typecheck/tests/build, Tauri
   guards/cargo check, Windows frozen-backend E2E, NSIS packaging/output verification, and installer
   upload all pass on the exact final PR head;
9. anchored self-review, comments/reviews/thread checks, `behind=0`, Ready transition, expected-head squash
   merge, exact-main verification, and exact-main post-merge `push` CI all pass before Block 8 is frozen.

## Consequences

Block 8 hardens the accumulated v1.1 desktop workflow by attacking avoidable request fan-out and
presentation pressure first, while keeping every scientific and durability contract intact. The result is
intended to make large projects materially more usable without turning performance infrastructure into a
new source of scientific truth or forcing a schema migration immediately before installed-desktop E2E
acceptance in Block 9.
