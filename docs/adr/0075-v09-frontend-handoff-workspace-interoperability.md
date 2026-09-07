# ADR-075: v0.9 Frontend Handoff and Workspace Interoperability

- Status: Accepted
- Date: 2026-09-07

## Context

v0.9 Blocks 1 through 7 established deterministic workspace/inventory/readiness projections,
scientific presentation DTOs, reporting/export, experiment-like application services, and a
path-scoped Python/CLI facade. A future desktop client therefore no longer needs direct access to
scientific storage internals, but one final application-facing boundary is still missing: a
versioned transport envelope that tells a frontend exactly which read/presentation contracts it is
receiving.

ADR-008 selected MatterViz as the preferred visualization candidate and Tauri + Svelte/TypeScript
with a local Python backend as the preferred desktop direction, subject to implementation
benchmarking. It also froze the key rule that visualization remains presentation-only and that the
Python scientific core must remain independently usable.

Block 8 must prepare that frontend handoff without turning a JavaScript framework, Tauri runtime,
window/session state, or frontend cache into scientific authority.

## Decision

### 1. Add one versioned frontend transport envelope

Block 8 adds `ecatvasp-frontend-handoff-v1` as a transient, deterministic read-only envelope.

The envelope contains:

- backend package version;
- explicit capability names and contract versions;
- MatterViz target version;
- one already-validated Block 5 `ScientificReport` payload, including workspace, inventory,
  provenance/freshness, optional workflow readiness, and optional scientific presentation DTOs.

The envelope does not invent a second workspace schema or duplicate scientific values.

### 2. Reuse the reporting validation boundary

`build_frontend_handoff()` delegates composition and ownership/currentness validation to
`build_scientific_report()`.

Consequently:

- a foreign structure presentation is rejected;
- stale workflow-generation dashboards remain fail-closed;
- caller-supplied authoritative inventory/freshness overrides remain visible;
- presentation source identities and hashes remain unchanged;
- report semantics remain identical to Block 5.

Block 8 does not reimplement those validators in frontend code.

### 3. Publish transport capabilities explicitly

The v1 handoff advertises these capabilities:

- `workspace-report` using `ecatvasp-scientific-report-v1`;
- `scientific-presentation` using `ecatvasp-scientific-presentation-v1`;
- `matterviz` using `ecatvasp-matterviz-v1`.

MatterViz's currently locked target version is transported separately so a desktop/web client can
check runtime compatibility without treating the JavaScript package as a Python scientific
dependency.

### 4. Handoff identity is non-scientific

`handoff_hash` is SHA-256 over canonical JSON of the transport envelope excluding the hash itself.
It is only suitable for transport equality, cache invalidation, or frontend refresh decisions.

It must never become:

- a `parameters_hash`;
- scientific identity;
- a provenance subject/source;
- a dependency-record hash;
- workflow-generation identity;
- a freshness authority.

### 5. The path-scoped facade exposes the handoff

`ProjectFacade.frontend_handoff()` reopens the current `ProjectStore` and builds the envelope from
that current validated bundle.

The facade still stores only a project path. A frontend holding an old envelope cannot make it
authoritative over later ProjectStore state.

### 6. Desktop technology remains downstream

Block 8 does not add Tauri, Svelte, TypeScript, Node, or MatterViz as Python runtime dependencies.
The implementation benchmark result is architectural rather than a production desktop package:

- Tauri + Svelte/TypeScript remains compatible with the local-Python-backend direction;
- the v1 JSON handoff is sufficient for a process/RPC boundary;
- MatterViz can consume its existing typed structure payload from the handoff;
- frontend local state may hold selection, panel, camera, color, axis-range, and window state, but
  none of it is scientific provenance or ProjectStore state;
- production desktop packaging/distribution remains a v1.0 concern.

### 7. Frontend clients must fail closed on incompatible contracts

A client must inspect `contract_version` and advertised capability versions before consuming the
payload. Unknown major contract versions are not silently coerced.

The Python v1 constructor likewise rejects duplicate capability names or unsupported handoff
versions.

### 8. Schema and scientific identity remain frozen

The frontend envelope is not persisted into `ProjectBundle` and introduces no permanent entity or
migration.

- package version remains `0.9.0.dev0`;
- `SCHEMA_VERSION` remains 3;
- runtime dependency set remains unchanged.

## Public contract

Python clients can use:

```python
from ecatvasp.api import open_project
from ecatvasp.frontend import render_frontend_handoff_json

project = open_project("/path/to/project")
handoff = project.frontend_handoff()
payload = render_frontend_handoff_json(handoff)
```

More specialized callers may supply current readiness and scientific presentation DTOs to the same
facade method. Those inputs still pass through Block 5 reporting validation.

## Acceptance contract

Block 8 is accepted only if tests prove that:

1. identical current scientific/application state produces byte-identical handoff JSON and the same
   non-scientific `handoff_hash`;
2. the envelope advertises exact report/presentation/MatterViz contract versions and MatterViz target
   version;
3. a structure presentation crosses the handoff with exact StructureSnapshot identity, source
   scientific hash, atom-index mapping, and MatterViz contract intact;
4. caller-supplied authoritative stale freshness remains visibly stale in the transport payload;
5. `ProjectFacade.frontend_handoff()` reopens later ProjectStore state rather than serving a cached
   envelope;
6. foreign/incompatible presentation input fails closed through the existing reporting boundary;
7. no Tauri/Svelte/Node runtime dependency is introduced and `SCHEMA_VERSION` remains 3.

## Non-scope

Block 8 does not add:

- a production desktop shell, installer, updater, or distribution pipeline;
- persisted window/session/panel state;
- frontend-owned workflow/scientific state;
- an HTTP/database server or multi-user collaboration layer;
- automatic scheduler submission;
- Study/high-throughput entities;
- NEB, solvation, constant-potential, microkinetics, ML, or other new scientific methods;
- tag, GitHub Release, or PyPI publication.

## Consequences

The future desktop client can now treat Python as the authoritative local backend and consume one
versioned JSON envelope rather than reaching into domain/storage internals. The frontend technology
can evolve independently while permanent scientific identity, provenance, freshness, and workflow
history remain owned by the existing Python core.
