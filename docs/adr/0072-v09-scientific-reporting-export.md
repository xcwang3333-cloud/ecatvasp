# ADR-072: v0.9 Scientific Reporting and Export

- Status: Accepted
- Date: 2026-09-07

## Context

ADR-067 reserves v0.9 Block 5 for deterministic scientific reporting and export. Blocks 1–4 now
provide stable application-facing sources for project summaries, provenance/freshness inspection,
workflow/execution readiness, and canonical scientific presentation datasets.

The remaining reporting problem is not to calculate another scientific result. It is to package those
existing authorities into auditable output that can be consumed by researchers, notebooks, CLI tools,
frontends, and archival workflows. The export must remain deterministic, preserve stale/blocked
visibility, retain identifiers/provenance/conditions/units, and never infer scientific meaning from
filenames or rendering choices.

## Decision

### 1. Reporting is a transient composition layer

Block 5 adds `ecatvasp.reporting` and report contract version:

`ecatvasp-scientific-report-v1`

`ScientificReport` composes:

- the existing `WorkspaceProjection`;
- the existing `WorkspaceScientificInventory`;
- zero or more existing `WorkflowReadinessDashboard` values; and
- zero or more Block 4 scientific presentation datasets.

The report is not inserted into `ProjectBundle`, is not a provenance subject, does not create a
`DependencyRecord`, and is not persisted workflow history.

`SCHEMA_VERSION` remains 3 and the package remains `0.9.0.dev0`.

### 2. Report construction consumes existing authorities

`build_scientific_report()` validates the supplied `ProjectBundle` and uses the existing
`build_workspace_projection()` authority.

By default it builds inventory through existing `build_scientific_inventory()`. A caller may instead
supply a prebuilt `WorkspaceScientificInventory`, which is required when the inspection projection
contains caller-observed hashes or explicit invalid/superseded overrides. Reporting never recreates
those freshness decisions. It verifies that a supplied inventory belongs to the same project, covers
the exact current provenance-capable entity set, and projects the exact persisted dependency set.

Supplied readiness dashboards are checked against the current persisted workflow generation using the
existing `resolve_workflow_binding_generations()` authority. Reporting does not recompute scientific
gates or orchestration actions; it only prevents an obsolete dashboard from being exported as current.

### 3. Report hash is export identity, not scientific identity

`ScientificReport.report_hash` is a deterministic SHA-256 of the canonical report payload excluding
the hash field itself. It exists for cache/export equality and audit of generated content.

The report hash is explicitly non-scientific:

- it is not returned by `scientific_hash()`;
- it is not recorded on scientific dependencies;
- it is not used by `FreshnessEngine`; and
- it cannot affect workflow gates, convergence, or analysis validity.

A change in report-visible freshness or source content may change `report_hash` without changing the
underlying workspace projection hash.

### 4. JSON is the complete deterministic manifest

`render_report_json()` serializes the complete report manifest using stable key ordering and a fixed
UTF-8 textual representation. The manifest retains:

- project identity, schema, entity counts, and separate lifecycle status domains;
- every scientific inventory row;
- exact scientific hash where available;
- full provenance references;
- incoming/outgoing dependency edges;
- scientific ancestor ids;
- authoritative freshness state and exact reason references;
- attention codes including stale/blocked/invalid/superseded visibility;
- supplied workflow readiness generations, gates, attempts, jobs, and orchestration actions;
- supplied Block 4 presentation payloads, including source/result hashes, units, definitions, and
  explicit scientific conditions.

No report field is derived from a filename.

### 5. CSV is a deterministic inspection export

`render_inventory_csv()` produces a normalized one-row-per-inventory-entity export. It preserves:

- entity identity/kind/label;
- lifecycle status domain and status;
- current scientific hash where supported;
- freshness state and reason codes;
- attention codes;
- scientific ancestor ids;
- provenance ids/tool versions; and
- exact incoming/outgoing dependency tokens.

CSV is deliberately an inspection table rather than an alternative scientific-results format.
Canonical numerical arrays remain available through the JSON presentation payloads and downstream
Block 4 contracts.

### 6. Markdown is human-readable but cannot hide scientific problems

`render_report_markdown()` creates deterministic report sections for:

- project inventory counts;
- separate Calculation/Analysis/ExecutionAttempt/Scheduler lifecycle states;
- every row requiring freshness/attention review;
- provenance records;
- dependency links;
- supplied workflow readiness and blocker/reason codes; and
- supplied scientific presentation metadata, conditions, units, descriptors, and source hashes.

The Markdown renderer may summarize large numerical arrays, but it may not suppress stale, invalid,
superseded, or blocked state. JSON remains the lossless full manifest.

### 7. Presentation inputs are validated but not recalculated

A structure presentation must reference a current project `StructureSnapshot` and its stored source
scientific hash must equal the snapshot's canonical scientific hash.

DOS/PDOS and COHP/ICOHP presentations must reference a `StructureSnapshot` in the project. Reporting
does not smooth, aggregate, normalize, change energy references, mirror spins, or apply `-COHP`.

A reaction-diagram presentation must belong to the same project. Reporting copies its explicit CHE
conditions, state/step values, descriptor definitions, units, source receipts, and hashes; it does not
rerun CHE, reaction stoichiometry, descriptor calculations, or pathway ordering.

### 8. Export format remains presentation state

JSON indentation, CSV quoting, Markdown tables, section order, and similar textual layout choices are
export/presentation concerns. They do not become scientific provenance or source inputs.

No plotting, document-generation, or template runtime dependency is added in Block 5. The
implementation uses Python standard-library serialization only.

## Acceptance criteria

Block 5 is accepted when:

- identical validated inputs produce byte-identical JSON, CSV, and Markdown outputs;
- a deterministic `report_hash` identifies report content without becoming scientific identity;
- stale/blocked/invalid/superseded information and exact freshness reason codes remain visible;
- full JSON retains project identifiers, separated lifecycle domains, scientific hashes, provenance,
  dependencies, freshness, readiness, presentation source hashes, definitions, conditions, and units;
- CSV retains deterministic source-linked inventory/provenance/dependency/freshness rows;
- Markdown explicitly surfaces attention states and workflow reasons rather than presenting them as
  successful/current;
- supplied inventory projections with observed-hash or invalid/superseded overrides are consumed
  without reporting recomputing those decisions;
- stale/foreign inventory, readiness, and presentation inputs fail closed;
- no filename inference or alternative scientific calculation is introduced;
- reports remain transient and do not enter persistence/provenance/freshness/workflow state;
- `SCHEMA_VERSION` remains 3, package version remains `0.9.0.dev0`, and no runtime dependency is
  added; and
- Ruff, mypy strict, pytest on Python 3.11/3.12/3.13, and MatterViz contract remain green.

## Consequences

Block 6 can expose a high-level `report` application operation that returns these exact deterministic
exports rather than inventing a separate reporting data model. Block 7 can map the same operation to
headless CLI commands, and Block 8 can hand the JSON manifest/presentation contracts directly to
frontend or desktop clients.

Block 5 does not produce PDF, Word, or publication figures. Those are downstream rendering products
and would require separate template/export contracts if added later.