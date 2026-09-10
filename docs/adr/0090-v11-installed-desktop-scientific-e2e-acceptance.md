# ADR-090: v1.1 Installed Desktop Scientific E2E Acceptance

- Status: Accepted
- Date: 2026-09-09
- Scope: v1.1 Block 9 — Installed Desktop Scientific E2E Acceptance

## Context

v1.1 Blocks 1–8 are frozen on the exact post-merge-CI-verified baseline
`main@13939e53eca2f9b1f51521a10381f924cb7f705a`.

The product already has substantial acceptance coverage:

- Python scientific/unit/E2E coverage for model, VASP input, execution, results, electronic analysis,
  thermochemistry, reaction pathways, provenance, freshness, and schema-3 reopen behavior;
- typed desktop IPC v1/v2 contract tests;
- Rust/Tauri transport guards and cargo checks;
- a Windows frozen-backend smoke that exercises the built sidecar against schema-3 ProjectStore data,
  typed actions, and ProjectStore tamper/drift rejection;
- NSIS bundle creation, build-tree output verification, and installer artifact upload.

That evidence is necessary but does not prove the final installed artifact. The Windows CI currently executes
its scientific smoke against the frozen sidecar before Tauri packaging, then verifies that an NSIS installer
file exists. It does not install that NSIS bundle into an isolated location and exercise the executables
copied by the installer from the installed path.

Block 9 therefore closes an acceptance gap rather than adding another product or scientific feature family.
Its primary test subject is the installed desktop artifact:

`installed ECatVASP desktop -> installed Tauri runtime -> installed frozen sidecar -> schema-3 ProjectStore`.

## Decision

### 1. Block 9 is acceptance and hardening only

Block 9 does not introduce new scientific equations, durable scientific entities, workflow state machines,
identity dimensions, schema versions, or general-purpose desktop RPCs.

The following remain frozen:

- `ProjectStore` as the only durable project authority;
- Python domain/workflow/VASP/execution/analysis/thermochemistry code as scientific authority;
- `SCHEMA_VERSION = 3`;
- package development version `1.1.0.dev0` and desktop development version `1.1.0-dev.0`;
- frozen six-operation `ecatvasp-desktop-ipc-v1` compatibility contract;
- typed, operation-specific `ecatvasp-desktop-ipc-v2` with fail-closed unknown fields;
- the prohibition on frontend inference of convergence, freshness, scientific identity/hash, atom identity,
  scheduler-success-as-scientific-success, or durable scientific state;
- all physics deferred beyond v1.1.

A new scientific capability discovered during acceptance requires a separate architecture decision rather
than being smuggled into Block 9.

### 2. The installed Windows artifact is a first-class acceptance subject

The Windows CI must install the exact NSIS installer produced by the current commit into a deterministic,
isolated, non-production test location and verify the installed layout.

At minimum the installed tree must contain the expected desktop executable and packaged backend sidecar.
The acceptance must use the files copied by the installer. A build-tree executable with the same name is not
accepted as evidence for the installed-path checks.

The CI may use NSIS silent-install arguments and a runner-temporary installation directory. Installation
must not require interactive user input.

### 3. Installed sidecar protocol acceptance reuses the production protocol

The installed backend sidecar must be exercised directly through its production stdio protocol.
Acceptance must verify:

- frozen IPC v1 compatibility remains available;
- v2 health/capability metadata exposes the complete supported typed operation vocabulary expected by the
  current desktop;
- malformed/unsupported protocol requests continue to fail closed;
- project-root operations resolve the exact schema-3 ProjectStore rather than a test-only in-memory store.

The acceptance harness may prepare deterministic project fixtures with existing Python test/core authority,
but once a step claims to exercise installed desktop scientific behavior, the read or mutation under test
must pass through the installed sidecar's production protocol.

### 4. Installed scientific acceptance composes existing authorities

Block 9 does not recreate scientific algorithms inside the packaging harness. It composes existing frozen
application services and fixtures into representative task-path evidence covering the v1.1 closed loop:

`project -> model -> calculation/workflow -> execution boundary -> result/promotion -> electronic analysis -> thermochemistry -> reaction presentation`.

Not every node must perform an expensive external calculation inside CI. The matrix must instead prove the
installed sidecar can open/reopen exact persisted scientific state and execute representative typed
operations at each authority boundary using deterministic repository fixtures.

Where existing Block-specific E2E tests already prove a scientific algorithm, Block 9 should assert the
installed product reaches and preserves that authority rather than duplicate all numerical assertions.

### 5. HPC acceptance remains deterministic and site-independent

Portable CI does not require access to a real SSH/Slurm cluster.

Execution acceptance uses existing deterministic adapters/fixtures to verify the separation of
`Calculation`, `ExecutionAttempt`, and `RemoteJob`, job-center readiness/recovery semantics, and the rule that
scheduler completion never implies scientific convergence.

A real institutional cluster remains a separate site-specific/manual acceptance activity and is not a
portable merge gate, consistent with ADR-085.

### 6. Installed app lifecycle evidence is distinct from full GUI automation

The installed Tauri desktop executable must at least be launchable from the installed path under CI and must
use the packaged sidecar/lifecycle contract rather than a source-tree backend override.

Block 9 does not claim pixel-level or full end-user GUI automation unless a stable repository-supported UI
automation mechanism is added with separate justification. Existing TypeScript contract tests and Rust/Tauri
transport guards remain the authority for command shapes and frontend/transport fail-closed behavior.

If CI cannot reliably create an interactive desktop session, process startup, packaged-resource resolution,
backend lifecycle/log evidence, and clean termination are acceptable installed-app lifecycle evidence.
The PR and ADR must describe the level actually tested without overstating it.

### 7. Project reopen, restart, recovery, and tamper boundaries are mandatory

Installed acceptance must cover representative durability/resilience behavior:

- create/open/reopen a schema-3 project from a path containing normal Windows path complexity;
- restart the installed backend and reopen the same project without changing permanent scientific IDs;
- preserve ProjectStore manifest/database integrity checks;
- reject database/manifest drift or tampering rather than silently recovering authoritative data;
- preserve stale/blocked semantics for representative upstream scientific drift;
- keep transient desktop request-generation guards as presentation state only.

The acceptance harness must never repair or rewrite a corrupted project behind the installed sidecar during
a step that claims to test failure handling.

### 8. Acceptance evidence is layered

Block 9 uses an explicit acceptance matrix rather than one opaque smoke test:

1. **Package layer** — exact NSIS artifact exists and installs silently into an isolated path.
2. **Installed layout layer** — expected desktop executable and backend sidecar exist in the installed tree.
3. **Backend protocol layer** — installed sidecar passes v1 compatibility and v2 typed health/fail-closed checks.
4. **Project durability layer** — schema-3 create/open/reopen/restart and integrity/tamper behavior.
5. **Scientific task layer** — representative installed-path typed operations traverse the v1.1 task chain
   using existing scientific authorities and deterministic fixtures.
6. **Desktop lifecycle layer** — installed Tauri executable launches against packaged resources/sidecar and
   terminates cleanly under the level of GUI automation actually supported by CI.
7. **Cleanup layer** — installed test artifacts/processes do not leak into subsequent CI steps.

Each failure should identify the layer that failed.

### 9. Deterministic assertions are preferred to wall-clock thresholds

Acceptance gates assert exact protocol versions, operation sets, project/scientific identity continuity,
artifact/provenance relationships, expected blocked/stale states, installed paths, and process exit/lifecycle
behavior.

Wall-clock timing may be diagnostic but is not the primary correctness gate. Block 9 must not encode fragile
runner-speed assumptions as scientific or product truth.

### 10. CI order makes installed acceptance a merge gate

The Windows packaging job must evolve from:

`build sidecar -> pre-package smoke -> build NSIS -> verify output -> upload`

to include:

`build sidecar -> pre-package smoke -> build NSIS -> install exact NSIS -> installed desktop scientific acceptance -> verify output -> upload`.

The existing pre-package frozen-backend smoke remains useful and should not be removed merely because
installed acceptance is added. The two checks answer different questions.

Block 9 cannot be accepted if installed-artifact acceptance is skipped while packaging otherwise succeeds.

### 11. Release state is unchanged by acceptance

Completing Block 9 completes the planned v1.1 implementation/acceptance roadmap, but does not itself create a
tag, GitHub Release, or PyPI publication. Release/publication remains a separate explicit decision after the
post-merge-CI-verified v1.1 baseline is frozen.

### 12. Acceptance-discovered durability defects may be hardened without semantic expansion

Installed acceptance may expose deterministic production defects inside an existing authority even when the
scientific equations and identity model are already frozen. Correcting such a defect is within Block 9 only
when the patch preserves the existing domain, provenance, schema, protocol, and scientific semantics.

Canonical durable Artifact identity is defined by the exact bytes persisted to disk. Writers that record
`size_bytes` and SHA-256 must therefore compute both from the same byte sequence they write, independent of
platform newline translation. Canonical JSON and other hashed durable records must use byte-exact writes and
byte-exact existing-file comparisons; text-mode newline conversion must not alter persisted Artifact identity.

This rule applies to the representative installed chain and to equivalent durable writers found by the same
repository-wide audit. It does not authorize changes to DOS parsing, descriptor definitions, thermochemistry,
CHE/reaction equations, provenance identity, schema3, IPC contracts, or ProjectStore authority.

## Implementation sequence

1. Add the installed-artifact acceptance harness and contract tests without changing scientific semantics;
   acceptance-discovered durability defects may receive the narrowly scoped hardening defined above.
2. Add deterministic NSIS silent-install and installed-layout verification to Windows CI.
3. Exercise installed sidecar v1/v2 health, project reopen/restart, fail-closed, and integrity/tamper paths.
4. Extend installed-path evidence across representative v1.1 task/scientific boundaries using existing
   fixtures and application authorities.
5. Add installed Tauri executable lifecycle evidence at the level reliably supported by GitHub-hosted
   Windows runners.
6. Reconcile the complete acceptance matrix against ADR-085/086/087/088/089 and all frozen v1.1 boundaries.
7. Run exact-head full CI, anchored self-review, discussion/thread/base guards, Ready transition,
   expected-head squash merge, exact-main verification, and exact-main post-merge `push` CI.

## Acceptance

Block 9 is accepted only when all of the following hold on the exact final PR head:

1. the current NSIS installer is actually installed into an isolated Windows CI path;
2. acceptance executes the installed desktop/backend artifacts rather than substituting build-tree binaries;
3. installed sidecar v1/v2 production protocol checks pass and unsupported input remains fail-closed;
4. schema-3 project open/reopen/restart preserves scientific identity and ProjectStore authority;
5. representative result/electronic/thermochemistry/reaction and execution-boundary evidence is reachable
   through installed-path production protocol without frontend scientific inference or test-only science;
6. ProjectStore tamper/drift and representative stale/blocked scientific states remain fail-closed;
7. installed desktop lifecycle evidence is recorded without overstating the level of GUI automation;
8. schema3, IPC v1, package development state, Python scientific authority, and all deferred-physics
   boundaries remain unchanged;
9. canonical durable Artifact bytes, `size_bytes`, and SHA-256 remain byte-identical and platform-independent
   across the acceptance-covered persistence chain;
10. Ruff, strict mypy, pytest on Python 3.11/3.12/3.13, MatterViz, desktop typecheck/tests/build, Tauri
   guards/cargo, Windows frozen-backend smoke, installed-artifact acceptance, NSIS/output verification, and
   installer upload all pass on the exact final head;
11. anchored self-review, comments/reviews/thread checks, `behind=0`, Ready transition, expected-head squash
    merge, exact-main verification, and exact-main post-merge `push` CI all pass before v1.1 is frozen.

## Consequences

Block 9 changes the meaning of Windows acceptance from “the source-tree build can produce an installer” to
“the exact installer can deploy a desktop whose installed executable/sidecar preserve the frozen v1.1
scientific and durability contracts.”

This closes the packaging-to-installed-product gap without creating a parallel scientific implementation,
a new persistence model, or a release-state side effect.
