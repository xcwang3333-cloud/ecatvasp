# ADR-074: v0.9 Headless CLI and Python Application Facade

- Status: Accepted
- Date: 2026-09-07

## Context

v0.9 Block 6 established `ProjectApplicationService` as the project-scoped application seam for
inspect, prepare, run, analyze, promote, and report operations. That service deliberately accepts a
`ProjectStore`, reopens durable state for each operation, and leaves all scientific/workflow
authority in the existing lower layers.

The next usability gap is a stable headless entry point for scripts, notebooks, shells, CI jobs, and
future frontend adapters. Users should not need to know storage-module construction details merely
to open a project, inspect current state, summarize status, or export an existing scientific report.
At the same time, a CLI must not become another workflow engine, infer scientific success from
scheduler state, cache stale project state, or define an alternative report/freshness calculation.

## Decision

### 1. Add a path-scoped Python facade

`ProjectFacade` stores only a filesystem path. It does not cache a `ProjectBundle`, workspace
projection, freshness result, workflow generation, or report.

Every public facade operation constructs a fresh `ProjectApplicationService(ProjectStore(path))`.
The Block 6 service then reopens the current durable store exactly as before.

`open_project(path)` is the stable convenience constructor for notebook/script use.

### 2. Keep the existing application service visible

The facade exposes `application()` to return a fresh `ProjectApplicationService`. It does not wrap
or erase the type-specific Block 6 mutation contracts.

Block 7 adds convenience operations for:

- `inspect()`;
- `status()`;
- `report()`.

Future Python callers that need prepare/run/analyze/promote continue to use the same returned
`ProjectApplicationService`, preserving the exact lower-level receipts and typed inputs.

### 3. `status` is a projection, not a lifecycle

`HeadlessProjectStatus` is transient and non-scientific. It derives from Block 1/2 inspection data
and keeps these namespaces separate:

- Calculation scientific lifecycle;
- Analysis lifecycle;
- ExecutionAttempt lifecycle;
- scheduler/RemoteJob state;
- provenance freshness state.

It may count rows requiring attention, but it must not synthesize one overall success state or treat
scheduler completion as scientific convergence.

### 4. Add a standard-library-only CLI

The package exposes both:

- console script: `ecatvasp`;
- module entry point: `python -m ecatvasp`.

The first stable command set is deliberately small:

- `ecatvasp --project PATH inspect`;
- `ecatvasp --project PATH status`;
- `ecatvasp --project PATH report --format {json,csv,markdown}`.

`inspect` and `status` support deterministic JSON output for automation. Text output is a human
summary of the same transient projection.

No third-party CLI framework is added; Block 7 uses Python `argparse` and preserves the existing
runtime dependency set.

### 5. CLI report output is exactly the Block 5/6 report output

The CLI does not implement a second report serializer. `report` delegates through `ProjectFacade` to
`ProjectApplicationService.report()` and writes the returned deterministic content verbatim.

JSON/CSV/Markdown semantics, `report_hash`, provenance, freshness, units, conditions, and source
links therefore remain owned by Block 5 reporting.

### 6. CLI errors fail closed

Missing or invalid ProjectStore state and explicit application-service resolution failures produce a
non-zero CLI return code and a concise stderr message. The CLI does not convert such failures into
an empty/current-looking project response.

Argument-shape errors remain owned by `argparse`.

### 7. No persisted CLI/application state

Neither `ProjectFacade`, `HeadlessProjectStatus`, parser state, command history, nor rendered output
is added to `ProjectBundle` or provenance/dependency records.

The CLI does not add session/task/run entities, background state, daemon state, or hidden workflow
history.

### 8. Schema and scientific identity remain frozen

Block 7 adds no scientific method, scientific identity field, schema migration, or runtime
dependency.

- package version remains `0.9.0.dev0`;
- `SCHEMA_VERSION` remains 3.

## Public contract

Python:

```python
from ecatvasp.api import open_project

project = open_project("/path/to/project")
inspection = project.inspect()
status = project.status()
report = project.report()
service = project.application()
```

CLI:

```text
ecatvasp --project /path/to/project inspect [--json]
ecatvasp --project /path/to/project status [--json]
ecatvasp --project /path/to/project report --format json|csv|markdown
```

## Acceptance contract

Block 7 is accepted only if tests prove that:

1. one `ProjectFacade` instance observes later ProjectStore changes rather than serving cached state;
2. status retains separate Calculation, Analysis, ExecutionAttempt, scheduler, and freshness counts;
3. repeated CLI inspection over unchanged durable state is deterministic;
4. CLI report output is byte-for-byte identical to the Block 6 application report rendering;
5. a missing/invalid project fails closed with a non-zero result and no fake project payload;
6. the console script is registered without a new runtime dependency;
7. `SCHEMA_VERSION` remains 3.

## Non-scope

Block 7 does not add:

- a desktop/frontend transport schema (Block 8);
- persisted shell history or session/task state;
- automatic scheduler submission;
- new generic mutation JSON protocols for scientific objects;
- a replacement parser, convergence classifier, workflow engine, freshness engine, or report engine;
- Study/high-throughput entities;
- NEB, solvation, constant-potential, microkinetics, ML, or other new scientific methods;
- tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP now has a common headless seam for Python and shell automation while keeping the mature
scientific core authoritative. Block 8 can serialize stable workspace/presentation handoff contracts
for a future desktop client without teaching that client how to reconstruct scientific state from
storage internals.
