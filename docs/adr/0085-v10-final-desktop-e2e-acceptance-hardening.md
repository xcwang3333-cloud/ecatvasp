# ADR-085: v1.0 Final Desktop E2E Acceptance and Hardening

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 Block 9

## Context

Blocks 1-8 established the production desktop boundary without changing ECatVASP scientific or
storage authority:

- schema-v3 `ProjectStore` remains the only durable project authority;
- `ecatvasp-desktop-ipc-v1` is the frozen six-operation local Python IPC contract;
- the stdio sidecar is stateless across project requests and process restarts;
- Tauri + Svelte/TypeScript consumes typed backend contracts without direct ProjectStore access;
- desktop preferences, diagnostics, restart metadata, and export receipts are non-scientific local
  application state;
- MatterViz is a presentation-only consumer with exact atom-index/source-hash guards;
- typed application actions delegate to existing Python authorities;
- the Windows-first package contains a frozen Python sidecar and a Tauri/NSIS desktop build;
- Block 8 added fail-closed runtime recovery and deterministic report export with Rust-side SHA-256
  verification against the exact Python-rendered bytes.

The remaining v1.0 risk is integration rather than missing feature surface. A collection of green unit
and block-level tests is not sufficient if project switching, backend restart, scientific drift,
frontend handoff, typed actions, and the packaged Windows sidecar have never been exercised as one
continuous contract.

ADR-077 therefore reserved Block 9 for final cross-layer acceptance and explicitly forbids using this
block to introduce new scientific methods, durable Study semantics, or another source of scientific
truth.

## Decision

Block 9 is an acceptance/hardening block only. It adds no new scientific entity, persistence model,
scientific identity dimension, generic mutation surface, or network control plane.

### 1. The persisted-project cross-layer path is the canonical final acceptance fixture

The final Python E2E fixture must exercise a real schema-v3 `ProjectStore` through the actual local
stdio sidecar module entry point rather than calling only in-process helpers.

One project contains:

- a persistent `StructureSnapshot`;
- a completed downstream `Analysis`;
- exact provenance for that Analysis;
- a `DependencyKind.SCIENTIFIC` edge from the StructureSnapshot to the Analysis.

A second independent project is used to prove project switching without cached/session authority.

The first sidecar process must prove, in one NDJSON session:

1. exact backend/protocol compatibility health handshake;
2. open project A;
3. project A status with lifecycle/freshness namespaces kept separate;
4. project A frontend handoff with exact structure/MatterViz identity;
5. deterministic application report receipt;
6. representative typed `prepare_workflow` application action;
7. switch to project B and read its own identity/status;
8. switch back to project A without project-B leakage.

The sidecar then exits through EOF. No implicit current-project session is allowed.

### 2. Backend restart must observe current durable state, not prior receipts

After the first process exits, the persisted project-A StructureSnapshot is scientifically modified
while retaining its permanent UUID. The scientific hash therefore changes while identity remains the
same.

A newly started sidecar process must reopen current ProjectStore state and prove that:

- status exposes downstream stale freshness and attention;
- the rebuilt current structure presentation uses the new exact source scientific hash;
- the downstream Analysis remains stale with the persisted `scientific_hash_changed` reason;
- a current presentation refresh does not convert that downstream stale state back to fresh.

The existing scientific-report authority must still reject the pre-drift presentation because its
source hash no longer matches the current same-UUID StructureSnapshot.

This explicitly proves that presentation refresh and backend restart cannot mask scientific drift.

### 3. Workflow readiness remains an explicit presentation namespace

The final E2E path verifies that `workflow_readiness` remains a distinct report/workspace namespace.
The desktop does not infer readiness from scheduler state, calculation labels, or UI state.

Detailed non-empty readiness semantics continue to be covered by the existing v0.9 workflow-gate /
readiness acceptance and the Block-5 TypeScript workspace contract fixture. Block 9 does not invent a
second automatic gate evaluator merely to make this final fixture non-empty.

### 4. Existing lower-layer acceptance remains part of the final contract

Block 9 intentionally composes rather than duplicates the following already-frozen acceptance:

- calculation, Analysis, ExecutionAttempt, and scheduler lifecycle namespaces remain separate;
- the TypeScript workspace parser preserves provenance/dependencies/stale reasons and rejects stale
  structure source hashes;
- MatterViz atom-index mapping is bijective and identity preserving;
- desktop preferences are written only under the operating-system app-config location and existing
  Rust tests prove preference writes do not modify project files;
- runtime diagnostics remain sanitized and cannot override scientific truth;
- report export uses create-new semantics and recomputes SHA-256 from exact content bytes before
  write;
- typed application actions remain explicit and there is no generic scientific mutation operation.

These tests remain mandatory in the same CI matrix and are therefore part of final acceptance even
when not restated inside the new Python fixture.

### 5. The frozen Windows sidecar smoke becomes a real packaged E2E gate

The Windows packaging job must exercise the PyInstaller-frozen backend through its actual stdio
protocol using two real ProjectStores, typed read/application operations, project switching, process
restart, and same-UUID scientific drift.

The packaged smoke must validate at least:

- package/protocol compatibility;
- schema-v3 project open;
- status namespace presence;
- deterministic frontend handoff and current source hash;
- deterministic report action;
- typed workflow preparation;
- A -> B -> A switching;
- restart from EOF without state loss;
- stale freshness after source scientific-hash drift.

Only after that packaged E2E succeeds may the job continue to Rust transport tests, Tauri NSIS build,
output verification, and installer artifact upload.

### 6. Final acceptance does not change release state

Block 9 keeps:

- package version `1.0.0.dev0`;
- `SCHEMA_VERSION = 3`;
- Python scientific runtime dependencies unchanged;
- no tag;
- no GitHub Release;
- no PyPI publication.

Completion of v1.0 development means the production desktop architecture is post-merge-CI verified.
A separate explicit release decision is still required before changing distribution/release state.

## Acceptance criteria

Block 9 is stable only when all of the following are true on one exact PR head and then on the exact
squash-merged main commit:

1. the new persisted-project cross-layer sidecar E2E passes;
2. same-UUID scientific drift produces `DependencyKind.SCIENTIFIC` staleness;
3. old presentation source hash is rejected while refreshed presentation cannot hide downstream stale;
4. project A/B switching and process restart show no cached/session authority;
5. report and workflow-preparation receipts match existing Python application contracts;
6. package stays `1.0.0.dev0` and schema stays v3;
7. Ruff, strict mypy, and pytest pass;
8. Python 3.11, 3.12, and 3.13 pass;
9. MatterViz public contract passes;
10. desktop Svelte/TypeScript typecheck, Vitest, Vite build, Rust tests, and cargo check pass;
11. the upgraded frozen Windows backend final E2E passes;
12. Windows Tauri NSIS build, output verification, and installer artifact upload pass;
13. anchored self-review has no unresolved blocker;
14. merge guard confirms unchanged exact head, `behind_by = 0`, no main drift, and no blocking review
    or unresolved thread;
15. squash merge succeeds with `expected_head_sha`;
16. exact-main post-merge push CI completes successfully across the same matrix.

## Deferred beyond v1.0

No deferred scientific feature is pulled into this acceptance block. Durable Study/sweep/ranking and
schema v4, NEB/transition-state workflows, solvation, charged/constant-potential methods,
microkinetics, ML screening, full Pourbaix, multi-user collaboration, and Linux/macOS production
packaging parity remain separate future architecture decisions.

## Consequences

The v1.0 completion claim becomes evidence-based: the same scientific drift and statelessness rules are
proven both in the normal Python sidecar and in the frozen Windows backend used by the production
package. The desktop remains an application/presentation client over the frozen scientific core rather
than a second scientific runtime.