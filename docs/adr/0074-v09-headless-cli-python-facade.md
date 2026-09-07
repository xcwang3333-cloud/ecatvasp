# ADR-074: v0.9 Headless CLI and Python Application Facade

- Status: Accepted
- Date: 2026-09-07

## Context

Block 6 established `ProjectApplicationService` as the project-scoped application boundary for
inspect, prepare, run, managed scientific-result analysis, explicit structure promotion, and report
operations. That service intentionally exposes typed Python authorities and lower-level receipts.
It is suitable for application code, but users and future non-Python clients still need a stable
headless entry surface that can open a project, inspect its state, check status, and export a report
without importing internal modules.

The repository currently has no console-script entry point and no CLI dependency. Adding a feature-
rich CLI framework would introduce a runtime dependency without improving the scientific contract.
Likewise, allowing a CLI to maintain its own project/session state would recreate the state-authority
problem v0.9 is designed to avoid.

## Decision

### 1. Add a versioned headless Python facade over Block 6

`HeadlessProjectFacade` wraps one `ProjectApplicationService`. It is opened from a project root via
`open_project(...)`, which creates the existing `ProjectStore` and Block 6 service.

The facade exposes read-oriented, JSON-compatible documents for:

- `inspect()` — project/workspace summary plus source-linked scientific inventory;
- `status()` — separate Calculation scientific, Analysis, ExecutionAttempt, and Scheduler lifecycle
  summaries plus exact attention/freshness rows;
- `report(...)` — the existing Block 5 deterministic JSON/CSV/Markdown export through Block 6.

The facade does not persist any document it returns. Its contract version is a presentation/API
compatibility identifier, not a scientific hash or schema version.

### 2. `inspect` serializes existing workspace/inventory authority without reinterpretation

The inspection document serializes exact ids, entity kinds, labels, lifecycle domains/statuses,
current scientific hashes, provenance records, incoming/outgoing dependency edges, scientific
ancestor ids, freshness states/reasons, and attention codes from the existing
`WorkspaceScientificInventory`.

It does not recalculate scientific hashes, freshness, provenance, or status. Ordering follows the
deterministic Block 1/2 projections.

### 3. `status` is a compact projection, not an aggregate success state

The status document contains four explicitly named lifecycle namespaces:

- `calculation_scientific`;
- `analysis`;
- `execution_attempt`;
- `scheduler`.

It must never collapse those namespaces into one `success`, `done`, or traffic-light status.
Attention rows reuse Block 2 `attention_codes` and freshness decisions rather than inventing CLI
severity rules.

### 4. Add a standard-library CLI only

Add the console script:

```text
ecatvasp = ecatvasp.cli:main
```

The CLI uses Python `argparse` and adds no runtime dependency. Initial commands are:

```text
ecatvasp inspect PROJECT_ROOT [--format text|json]
ecatvasp status PROJECT_ROOT [--format text|json]
ecatvasp report PROJECT_ROOT --format json|csv|markdown
```

`python -m ecatvasp ...` uses the same `main()` entry point.

The CLI is intentionally thin: argument parsing, project opening, deterministic rendering, stdout,
stderr, and process exit code. It does not read or write hidden CLI state, select a scientific result
by filename, or reproduce application-service decisions.

### 5. CLI commands are read-only in Block 7

Block 6 already provides typed mutation operations whose inputs include scientific/workflow objects
that require exact identity and gate context. Encoding those operations as ad-hoc CLI flags in Block
7 would risk weakening type-specific scientific contracts.

Therefore the first stable headless CLI exposes project-open/inspect/status/report workflows only.
Python clients can use the full `ProjectApplicationService` through the facade's `service` property.
A future CLI mutation command may be added only when its exact typed input contract can be serialized
without inference or authority loss.

### 6. Text rendering is presentation-only

Human-readable `inspect` and `status` text is derived from the same headless documents used for JSON.
Text labels, indentation, and ordering are not scientific state and never enter provenance or
freshness.

JSON rendering is deterministic (`sort_keys=True`, UTF-8, stable newline). No timestamps are added by
the headless facade merely to record when a command was run.

### 7. Errors are explicit and do not mutate the project

A missing/invalid project, unsupported schema, invalid command arguments, or application/reporting
validation failure yields a non-zero CLI exit code and a concise stderr message. The CLI does not
repair project metadata implicitly.

`argparse` usage errors retain exit code 2. Runtime project/application errors use exit code 1.
Successful commands use exit code 0.

### 8. Schema, scientific identity, and dependencies remain unchanged

Block 7 adds no ProjectBundle entity, no migration, no scientific identity dimension, and no new
runtime package.

- package remains `0.9.0.dev0`;
- `SCHEMA_VERSION` remains 3.

The only packaging change is the console-script entry point.

## Public contract

Python:

```python
facade = open_project(path)
inspection = facade.inspect()
status = facade.status()
report = facade.report(format="markdown")
```

The inspection/status documents provide `to_dict()` and deterministic JSON/text renderers. The
facade retains access to the Block 6 `ProjectApplicationService` for typed Python application use.

CLI:

```text
ecatvasp inspect ./project
ecatvasp inspect ./project --format json
ecatvasp status ./project --format json
ecatvasp report ./project --format markdown
```

## Acceptance contract

Block 7 is accepted only if tests prove that:

1. `open_project()` and every facade read operation observe current `ProjectStore` state rather than
   cached state;
2. inspect JSON retains exact project ids, schema, entity counts, provenance/dependency ids,
   scientific hashes, freshness reasons, and separated lifecycle domains;
3. status output keeps Calculation scientific / Analysis / ExecutionAttempt / Scheduler namespaces
   separate and preserves attention codes;
4. deterministic facade JSON/text/report calls are byte-stable for identical durable state;
5. CLI `inspect`, `status`, and `report` use the same facade contracts and return exit code 0 on a
   valid project;
6. invalid project roots fail with exit code 1 and do not create metadata;
7. CLI parsing errors retain standard exit code 2;
8. `python -m ecatvasp` delegates to the same CLI entry point;
9. no CLI/session state is added to `ProjectBundle`, no runtime dependency is added, and
   `SCHEMA_VERSION` remains 3.

## Non-scope

Block 7 does not add:

- scheduler submission, monitoring daemons, or background execution;
- generic CLI flags for prepare/run/analyze/promote scientific mutations;
- shell completion frameworks or third-party CLI dependencies;
- frontend/desktop transport contracts (Block 8);
- server/API hosting;
- high-throughput `Study`, NEB, solvation, constant-potential, microkinetics, ML, or other new
  scientific methods;
- tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP gains a stable, dependency-light headless interface that can be used from shell scripts,
notebooks, CI systems, and future desktop/frontend adapters. All observable scientific state still
comes from `ProjectStore` and the existing workspace/provenance/application/reporting authorities.