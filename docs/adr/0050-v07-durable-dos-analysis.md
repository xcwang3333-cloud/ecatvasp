# ADR-050: v0.7 Durable DOS/PDOS Analysis Materialization Boundary

- Status: Accepted
- Date: 2026-09-05
- Scope: v0.7 Block 3

## Context

ADR-048 defines the electronic-analysis domain boundary and ADR-049 adds fail-closed DOSCAR
normalization. Block 2 deliberately stops at immutable in-memory facts. Block 3 must make those facts
reopenable, content-addressed, provenance-complete scientific results without changing the frozen
schema-v3 storage model or weakening the distinction between Calculation, execution, parsing, and
scientific freshness.

The existing v0.5 result-provenance path already establishes the preferred pattern: an Analysis owns an
analysis-produced Artifact, both receive ProvenanceRecords, and exact scientific inputs are connected
through SCIENTIFIC DependencyRecords.

## Decision

### 1. One canonical DOS Analysis owns parsed total DOS and atom/lm PDOS facts

One completed `AnalysisType.DOS` represents one exact canonical parse of a converged `DOS_STATIC`
Calculation. Its durable dataset contains the system DOS and the atom/lm-resolved projected series
already normalized by ADR-049.

Block 3 does not create a duplicate `AnalysisType.PDOS` result for the same arrays. `PDOS` remains
available for later explicit selector, grouping, aggregation, or transformed projected-DOS products.
In particular, element aggregation is not relabelled as a raw parser fact.

### 2. Scientific convergence is a hard prerequisite

Durable DOS materialization accepts only `CalculationType.DOS_STATIC` with
`CalculationScientificStatus.CONVERGED`.

An exited scheduler job, a retrieved DOSCAR, or a successful process return code is insufficient. The
existing separation between execution success and scientific convergence remains authoritative.

### 3. Exact managed source ownership is verified before materialization

The materializer requires:

- the exact DOSCAR Artifact whose SHA-256 equals the parser receipt;
- the exact calculation-produced `atom-index-map.json` Artifact whose SHA-256 equals the canonical
  result's frozen atom-map digest;
- the exact ExecutionAttempt that produced the DOSCAR;
- the exact Calculation that produced the atom-index map;
- local bytes whose size and SHA-256 still match Artifact metadata;
- the canonical result StructureSnapshot id equal to the Calculation input snapshot.

The ExecutionAttempt is checked to prevent source misattribution, but it is not part of the scientific
identity or SCIENTIFIC dependency DAG. Retrying the same scientific Calculation therefore does not
become a new scientific dependency merely because runtime identity changed.

### 4. Analysis scientific identity contains an exact source receipt

The DOS Analysis consumes exactly two input Artifact ids in semantic order:

1. DOSCAR;
2. frozen atom-index map.

Its `parameters_hash` is the deterministic hash of a source receipt containing the parser name/version,
StructureSnapshot id, exact input Artifact ids, and exact content hashes. The Analysis therefore cannot
silently survive a source-content or parser-version change.

No scheduler id, host, queue, credential, working directory, or ExecutionAttempt id enters this source
receipt.

### 5. The normalized dataset is an Analysis-produced durable Artifact

The canonical result is written atomically to:

`analyses/<analysis-id>/canonical-dos.json`

The output is `ArtifactType.DERIVED_DATASET`, produced by `AnalysisProducerRef`, locally retained with
`RetrievalPolicy.ALWAYS`, and content-addressed by SHA-256.

The payload records:

- format/version;
- Calculation and Analysis ids;
- exact source receipt and its hash;
- canonical result content hash;
- the normalized `CanonicalDosResult`.

An existing path with different content is rejected rather than overwritten.

### 6. Provenance and freshness reuse the existing SCIENTIFIC DAG

Block 3 creates ProvenanceRecords for the DOS Analysis and its derived Artifact. Its scientific DAG is:

`Calculation -> DOS Analysis`

`DOSCAR Artifact -> DOS Analysis`

`atom-index-map Artifact -> DOS Analysis`

`DOS Analysis -> canonical DOS Artifact`

All four edges are `DependencyKind.SCIENTIFIC` and capture the upstream scientific hash at production
time. The existing `FreshnessEngine` therefore propagates source drift to the Analysis and output
without an electronic-structure-specific freshness engine.

ExecutionAttempt is intentionally absent from this DAG.

### 7. Durable reopen revalidates bytes and semantic identity

A canonical loader verifies the analysis type/status, `AnalysisProducerRef`, artifact type, local path,
size/SHA-256, payload format/version, Analysis id, source receipt hash, Analysis input ids, result content
hash, StructureSnapshot identity, and frozen atom-map digest before reconstructing the canonical value.

`ProjectStore` persistence remains the durable metadata authority. A save/open roundtrip must preserve
the Analysis, Artifact, ProvenanceRecords, and DependencyRecords without a migration.

### 8. No schema or dependency expansion

`SCHEMA_VERSION` remains 3. The implementation uses existing `Analysis`, `Artifact`,
`AnalysisProducerRef`, `ProvenanceRecord`, `DependencyRecord`, `ProjectBundle`, and `ProjectStore`
contracts. No runtime dependency is added.

## Deferred

Block 3 does not implement:

- element/group/orbital aggregation products beyond the raw normalized projections;
- band, d-band, or p-band centers;
- Bader analysis;
- charge-density subtraction;
- LOBSTER, COHP, or ICOHP;
- workflow analysis reconciliation beyond the already reusable provenance/freshness graph;
- thermochemistry or electrochemical free-energy analysis.

## Consequences

DOS/PDOS parser facts now have a single durable, reopenable scientific identity with exact source and
parser provenance. Runtime retry identity remains outside scientific freshness, while changes to the
Calculation, DOSCAR, frozen atom map, parser receipt, or normalized result are all detectable without a
new storage schema or a second state machine.
