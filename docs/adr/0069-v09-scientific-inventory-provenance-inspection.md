# ADR-069: v0.9 Block 2 Scientific Inventory and Provenance Inspection

- Status: Accepted
- Date: 2026-09-07

## Context

ADR-067 defines v0.9 as the Research Workspace and Scientific Presentation phase, and ADR-068
establishes a pure project-level workspace projection. The next usability gap is source inspection:
a researcher needs to identify exactly which persisted scientific object is being shown, where it
came from, what it depends on, whether those dependencies are still current, and which lifecycle
namespace owns any visible status.

ECatVASP already has the scientific authority required for this task. `ProjectBundle` owns the
validated project graph; `ProvenanceRecord` and `DependencyRecord` provide exact source links;
`scientific_hash()` defines existing scientific hashes; and `FreshnessEngine` evaluates scientific
drift while deliberately ignoring organizational, display, and execution dependency kinds for
scientific invalidation.

Block 2 must expose those facts without creating a second provenance or freshness implementation.

## Decision

### 1. Scientific inventory is a read model over provenance-capable project entities

`build_scientific_inventory()` consumes a validated `ProjectBundle` and emits immutable
`WorkspaceInventoryRow` values for the existing provenance-capable entity families:

- Project;
- Catalyst;
- StructureVariant;
- StructureSnapshot;
- ActiveSite;
- AdsorptionState;
- StateConformer;
- MethodFingerprint;
- Calculation;
- ExecutionAttempt;
- RemoteJob;
- Artifact; and
- Analysis.

Each row retains the exact entity UUID and a presentation-only entity-kind label. The inventory does
not create new scientific entity identifiers.

### 2. Provenance and dependency records are projected exactly

Inventory rows expose exact persisted provenance ids, subject ids, tool/version,
`parameters_hash`, optional `method_fingerprint_id`, timestamps, dependency ids, upstream/downstream
ids, dependency kind, role, and recorded scientific hash.

No source relation is inferred from filenames, directory layout, display labels, or adjacency in the
inventory.

### 3. Existing FreshnessEngine remains the sole freshness authority

Block 2 computes current canonical hashes only through the existing `scientific_hash()` function and
passes them to the existing `FreshnessEngine`. An optional `observed_hashes` mapping may replace a
known current hash when an application has independently observed current source content. Such
values are presentation inputs only; they are validated as SHA-256 digests and are never persisted by
the inventory.

If a scientific dependency requires an upstream hash that cannot be established, the existing engine
continues to fail closed with `scientific_hash_missing`. Scientific hash drift remains
`scientific_hash_changed` and propagates only across `DependencyKind.SCIENTIFIC` edges.

Block 2 does not implement a second freshness algorithm.

### 4. Lifecycle state and freshness remain separate dimensions

Inventory rows preserve the namespace of lifecycle states:

- Calculation status -> `calculation_scientific`;
- Analysis status -> `analysis`;
- ExecutionAttempt status -> `execution_attempt`; and
- RemoteJob state -> `scheduler`.

A blocked Analysis may therefore be freshness-fresh, and scheduler completion is never interpreted as
scientific convergence.

`attention_codes` are presentation hints derived only from explicit lifecycle values and freshness
state. They are not persisted scientific verdicts and are not replacements for authoritative gate
reason codes.

### 5. Blocked-cause inference is deliberately deferred to Block 3

A generic entity status such as `BLOCKED` does not contain enough information to reconstruct the
causal workflow gate. Block 2 exposes that the entity is blocked, but it does not invent a causal
reason.

Block 3 will project authoritative workflow/reconciliation gate reason codes from the existing gate
APIs. This avoids teaching the inventory layer to duplicate workflow semantics.

### 6. Scientific lineage is explicit dependency ancestry

Each row exposes deterministic transitive ancestor ids reachable only through persisted
`DependencyKind.SCIENTIFIC` edges. This is a convenience projection over exact dependency records,
not a new persisted lineage model.

Display, organizational, and execution edges remain inspectable as direct dependencies but are not
included in scientific ancestry.

### 7. Invalid lifecycle states may seed the existing freshness override

An explicitly `INVALID` Calculation or Analysis is passed as an invalid override to the existing
`FreshnessEngine`, so downstream scientific invalidation remains consistent with the current domain
state.

Workflow binding supersession is not independently inferred here. Authoritative workflow-generation
supersession remains the responsibility of existing workflow gate evaluation and will be presented
in Block 3. Optional explicit superseded ids are accepted only as caller-supplied freshness evidence.

### 8. No persistence, schema, or runtime-dependency expansion

Inventory, provenance views, dependency views, freshness views, and attention codes are transient
application/read models. They are not added to `ProjectBundle`, do not become provenance subjects,
and do not participate in scientific identity.

`SCHEMA_VERSION` remains 3. No migration and no new runtime dependency are introduced.

## Acceptance criteria

Block 2 is accepted when:

- a validated project produces deterministic source-linked inventory rows;
- exact entity/provenance/dependency UUIDs are preserved;
- provenance tool/version, parameters hash, method id, timestamp, dependency role/kind, and recorded
  hash remain visible without filename inference;
- Calculation, Analysis, ExecutionAttempt, and scheduler lifecycle namespaces remain distinct;
- canonical scientific hashes come only from the existing `scientific_hash()` authority;
- observed hash drift is exposed through the existing `FreshnessEngine` reason codes;
- missing scientific source hashes fail closed;
- DISPLAY/ORGANIZATIONAL/EXECUTION dependencies do not propagate scientific staleness;
- scientific ancestry traverses only exact `DependencyKind.SCIENTIFIC` edges;
- explicit invalid Analysis/Calculation lifecycle state feeds the existing freshness invalidation
  mechanism;
- generic BLOCKED lifecycle state is exposed without fabricating workflow causal reasons;
- no workspace inspection entity, hash, or attention code is persisted as scientific state;
- `SCHEMA_VERSION` remains 3 and package version remains `0.9.0.dev0`; and
- Ruff, mypy strict, pytest on Python 3.11/3.12/3.13, and MatterViz contract remain green.

## Consequences

Block 3 can build a workflow/execution readiness dashboard on top of exact inventory rows while
projecting authoritative workflow generations, gate verdicts, reconciliation results, and causal
blocker reason codes. Reporting and frontend layers can later consume the same source-linked rows
without traversing raw `ProjectBundle` internals or reimplementing freshness semantics.
