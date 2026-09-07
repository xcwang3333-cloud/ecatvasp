# ADR-082: v1.0 Typed Desktop Application Actions

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 Block 6

## Context

Blocks 1–5 established a stateless local Python IPC boundary, a Tauri/Svelte desktop shell,
desktop-local project lifecycle preferences, and authoritative scientific workspace/readiness/
presentation views. The desktop can now inspect current `ProjectStore` state but cannot yet invoke any
scientific/project application operation.

`ProjectApplicationService` already owns the application-level orchestration seam. It reopens the
current store at call time and delegates to existing domain/workflow/VASP/reporting authorities. It
must remain the owning mutation boundary rather than being reimplemented in TypeScript or Rust.

Block 6 therefore needs useful desktop actions without turning IPC into a generic entity mutation API.

## Decision

### 1. Add only explicitly typed operations

Block 6 adds two desktop IPC operations:

- `application_report`
- `prepare_workflow`

Each operation has a fixed request schema and a fixed receipt schema. Unknown fields fail closed.
There is no `{entity, patch}`, arbitrary JSON payload, generic method name, or dynamic mutation
registry.

The transport remains `ecatvasp-desktop-ipc-v1`; adding explicitly advertised operations is a
backward-compatible v1 capability extension. Health continues to advertise the exact supported
operation set.

### 2. `application_report` delegates exactly to `ProjectApplicationService.report()`

The request carries only:

- explicit `project_root`;
- explicit report format (`json`, `csv`, or `markdown`).

The receipt contains the lower-layer report contract version/hash, requested format, deterministic
content hash, and rendered content. Report and content hashes remain presentation/export identity;
they do not become scientific identity or provenance hashes.

No desktop file export is performed in this block. Durable/export-path behavior remains Block 8.

### 3. `prepare_workflow` delegates exactly to the canonical workflow registry and application service

The request carries only:

- explicit `project_root`;
- exact canonical workflow recipe id;
- exact canonical workflow recipe version;
- exact root `StructureSnapshot` UUID;
- optional explicit parameters SHA-256.

The backend resolves the recipe through the canonical Python workflow registry and then calls
`ProjectApplicationService.prepare_workflow()`.

The receipt returns the exact persisted/reused `ScientificWorkflowPlan` id, plan hash, planning hash,
root snapshot id, recipe identity, and reuse flag. The desktop never calculates those values itself.

### 4. Canonical workflow recipe choices come from Python

Health advertises package-level canonical workflow recipe metadata for the frontend selector. Recipe
ids, versions, and descriptions are read from `list_workflow_recipe_specs()`.

The frontend may display and select these values but must not duplicate workflow graphs, VASP recipe
composition, or scientific defaults.

### 5. More evidence-heavy mutations remain intentionally unexposed in Block 6

The following existing Python operations are not placed on the desktop wire in this block:

- `prepare_workflow_step()`;
- `run_workflow()`;
- `analyze_vasp_result()`;
- `promote_vasp_structure()`.

Those operations require exact current orchestration generations, `ExecutionPlan`, scheduler/recovery
handoffs, parsed result/intake evidence, or convergence evidence. Flattening those objects into a
loosely typed desktop JSON payload would violate the existing scientific boundaries.

Their lower-layer fail-closed rules remain authoritative, including:

- obsolete/superseded workflow generations cannot be used as current;
- scheduler success is not scientific convergence;
- unconverged calculations cannot promote structures;
- foreign ids/evidence fail closed;
- UID-bound result analysis requires the exact managed `ExecutionPlan`.

The desktop cannot bypass these gates because there is no wire operation for them.

### 6. Action failures are structured application rejections

Recognized project-storage failures continue to return `project_unavailable`.

Known value/contract failures raised while executing the two typed application actions return
`application_rejected` with no success payload. Unexpected programming/runtime failures are not
silently converted into scientific/application facts.

### 7. Desktop action state is transient

Form values, busy/error state, report previews, and the last action receipt are frontend-local state.
They are not written into `ProjectBundle`, provenance, workflow history, or scientific hashes.

After a successful mutating `prepare_workflow` action, the frontend reloads the authoritative current
handoff from the explicit project path. It does not patch local workspace state from the action
receipt.

### 8. Rust remains a transport guard

Tauri validates the exact allowed operation-specific request fields before forwarding one NDJSON
request to Python. Rust does not inspect scientific meaning or reconstruct receipts.

## Request contracts

### `application_report`

Required fields:

- `protocol_version`
- `request_id`
- `operation = "application_report"`
- `project_root`
- `report_format`

No workflow fields are permitted.

### `prepare_workflow`

Required fields:

- `protocol_version`
- `request_id`
- `operation = "prepare_workflow"`
- `project_root`
- `workflow_recipe_id`
- `workflow_recipe_version`
- `root_structure_snapshot_id`

Optional:

- `parameters_hash` (exact SHA-256)

No report fields are permitted.

## Acceptance

Block 6 is accepted only if:

1. the exact Block 5 stable main is the branch base;
2. report JSON/CSV/Markdown round-trip through desktop IPC and match
   `ProjectApplicationService.report()` receipts;
3. workflow preparation round-trips through IPC and persists/reuses the exact application-service
   workflow plan;
4. unknown recipe ids/versions fail closed;
5. malformed/foreign root snapshot ids fail closed without a fake success receipt;
6. operation-specific unknown/cross-operation fields are rejected by Python and Rust transport guards;
7. the frontend requires the health handshake and sends only typed action fields;
8. a successful workflow mutation triggers a fresh path-scoped handoff reload rather than local state
   patching;
9. action form/report-preview state remains desktop-local;
10. existing tests for unconverged promotion, obsolete workflow generations, scheduler/scientific
    separation, and UID-bound result evidence remain green;
11. `SCHEMA_VERSION` remains 3 and package version remains `1.0.0.dev0`;
12. no Python scientific runtime dependency is added;
13. exact-head CI passes Ruff, strict mypy, pytest, Python 3.11/3.12/3.13, MatterViz contract, Svelte/
    TypeScript checks, Vitest, Vite build, Tauri tests, and `cargo check`;
14. the normal review/merge/post-merge exact-main CI guard completes successfully.

## Consequences

The desktop gains its first safe application/mutation workflow while retaining a narrow attack and
semantic surface. It can create/reuse durable workflow intent and render deterministic scientific
reports, but it still cannot bypass lower-layer convergence, generation, execution, or provenance
gates.

More complex actions may be added only as separate type-specific contracts once their exact evidence
can be represented without generic mutation semantics.
