# ADR-084: v1.0 Desktop Resilience, Security, Diagnostics, and Safe Exports

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 Block 8

## Context

Blocks 1–7 established a versioned local stdio backend, a Tauri + Svelte/TypeScript desktop shell,
path-scoped ProjectStore access, typed application actions, MatterViz/scientific presentation views,
and a Windows-first packaged runtime. The Block 7 stable baseline can build and smoke the frozen
Python sidecar and ship it inside a Tauri NSIS application without changing schema v3 or Python
scientific authority.

The remaining pre-E2E product risk is operational rather than scientific. A desktop process can lose
its sidecar, receive an incompatible response, encounter corrupt/missing projects, or need to persist a
deterministic report to a user-selected local directory. Those failures must not be interpreted as
scheduler/scientific state, and recovery must never patch cached frontend state into ProjectStore.

## Decision

### 1. Runtime recovery remains outside scientific IPC

The frozen Python desktop IPC remains `ecatvasp-desktop-ipc-v1` with the same six operations:

- `health`;
- `open_project`;
- `status`;
- `frontend_handoff`;
- `application_report`;
- `prepare_workflow`.

Backend restart, diagnostics, and local file export are Tauri runtime/application functions. They do
not become Python scientific operations, ProjectBundle entities, provenance records, workflow facts,
or freshness inputs.

### 2. Transport failure invalidates the local process handle

A successful health handshake is required before project requests. If the Tauri transport cannot
write/read the sidecar, receives malformed/correlated-incompatible output, or fails compatibility
validation, the current process handle is discarded and the client is considered not ready.

No request is silently retried. The user may explicitly restart the backend. After a successful
restart the desktop reopens/reloads the selected project through its exact ProjectStore path. It does
not reconstruct scientific state from a prior receipt or frontend cache.

Application-level Python rejections such as `project_unavailable` or `application_rejected` are not
transport crashes and therefore do not by themselves invalidate a healthy sidecar.

### 3. Diagnostics are intentionally minimal and sanitized

Tauri exposes a local runtime diagnostics surface containing only:

- backend runtime state (`not_started`, `starting`, `ready`, `exited`, or `unavailable`);
- successful explicit restart count;
- the last coarse failure category (`spawn`, `transport`, `compatibility`, or `shutdown`) when one
  exists.

Diagnostics do not expose executable paths, process ids, environment variables, project paths,
request/response bodies, VASP files, credentials, tokens, full exception strings, scheduler facts, or
scientific convergence claims. Detailed OS/process errors remain internal and are mapped to stable,
non-sensitive frontend messages.

### 4. Deterministic report export persists exact Python-produced bytes

`ProjectApplicationService.report()` remains the report authority. Python returns the exact report
content together with its format, report hash, and content SHA-256. The desktop export command only
writes those already-rendered bytes to an explicit local directory.

The export command:

- accepts only `json`, `csv`, or `markdown` report formats;
- requires an existing absolute output directory;
- does not accept a user-supplied filename;
- derives the filename solely from the existing content SHA-256 and format:
  `ecatvasp-report-<content_sha256>.<extension>`;
- never infers species, structure, reaction, project, or scientific identity from a filename;
- uses create-new semantics and never overwrites different existing content;
- treats an exact existing byte-for-byte target as an idempotent reuse;
- enforces a bounded local content size;
- returns only a non-scientific export receipt (filename, format, content hash, byte count, reused).

Export does not create or mutate ProjectBundle state. Exporting an older exact report receipt also does
not make that report current; scientific freshness remains whatever the Python report/handoff
contracts stated when the receipt was generated.

### 5. Client recovery is fail-closed

The TypeScript client marks itself not-ready on Tauri invocation failure or malformed/incompatible
transport responses. A later project request must fail locally until a new successful `connect` or
explicit `restart` health handshake occurs.

Runtime diagnostics remain callable while the backend is not ready. A successful restart changes only
runtime readiness; project scientific state is reloaded from Python/ProjectStore before presentation.

### 6. No new scientific or storage dependency

Block 8 adds no Python runtime dependency, permanent entity, migration, database service, network
listener, or scientific identity dimension.

- `SCHEMA_VERSION` remains 3;
- package version remains `1.0.0.dev0`;
- Python scientific runtime dependencies remain ASE and NumPy only;
- logs/preferences/diagnostics remain outside ProjectBundle;
- report/provenance/freshness contracts remain owned by their existing Python layers.

## Rejected alternatives

### Add restart/diagnostics/export as Python IPC scientific operations

Rejected because they are desktop runtime concerns and would unnecessarily expand the versioned
scientific/application seam.

### Automatically retry failed scientific/application requests after a crash

Rejected because replaying a mutation after an uncertain transport failure can duplicate or obscure
the actual durable outcome. Recovery requires a new health handshake and current ProjectStore reload.

### Let the frontend choose arbitrary export filenames

Rejected because it creates path traversal/overwrite surface and encourages scientific meaning to leak
into filenames. Content-hash naming is deterministic and semantics-neutral.

### Persist runtime diagnostics into ProjectBundle

Rejected because process crashes, restart counts, and installer/runtime observations are not scientific
history or provenance.

## Consequences

Positive:

- dead/incompatible backend processes cannot remain silently authoritative in the client;
- recovery is explicit and reconstructs presentation from durable ProjectStore state;
- diagnostics are useful without becoming a sensitive log dump;
- deterministic reports can be exported without modifying scientific state;
- safe export naming prevents path traversal and filename-based scientific inference.

Costs/risks:

- explicit recovery can require the user to retry an application action after checking durable state;
- diagnostics are deliberately coarse and may require external developer logs for deep debugging;
- export directories are entered/selected explicitly and must already exist;
- old report receipts remain exportable by design, so UI wording must not imply that export refreshes
  scientific freshness.

## Invariants

Block 8 must preserve all prior scientific and desktop boundaries. In particular:

- Calculation, ExecutionAttempt, and RemoteJob remain distinct;
- scheduler success never means scientific convergence;
- ProjectStore and Python application/scientific services remain authoritative;
- frontend/runtime recovery never patches scientific state locally;
- stale/blocked report and presentation semantics remain unchanged;
- no generic mutation engine is introduced;
- no network listener is introduced;
- runtime diagnostics and export receipts are non-scientific.

## Block 8 acceptance

Block 8 is stable only after:

1. backend transport failure invalidates readiness and explicit restart performs a new health handshake;
2. project/workspace state is reloaded from exact ProjectStore paths after recovery;
3. diagnostics expose only sanitized runtime state/restart/failure categories;
4. corrupt/missing projects and incompatible contracts continue to fail closed;
5. report export is deterministic, bounded, hash-named, traversal-resistant, and non-overwriting;
6. export receipts do not become ProjectBundle/provenance state;
7. Python IPC operations, schema v3, package version, and scientific runtime dependencies remain frozen;
8. Svelte/TypeScript/Vitest, Rust tests/checks, Python 3.11/3.12/3.13, Ruff, strict mypy, pytest,
   MatterViz, and Windows packaged sidecar/NSIS CI all pass;
9. Draft PR self-review, exact-head CI, Ready/merge guard, squash merge, and exact-main post-merge CI
   all succeed.
