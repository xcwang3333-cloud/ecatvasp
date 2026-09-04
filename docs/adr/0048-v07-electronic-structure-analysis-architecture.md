# ADR-048: v0.7 Electronic Structure and Analysis Architecture

- Status: Accepted
- Date: 2026-09-04
- Scope: v0.7 architecture review and Block 1

## Context

v0.1-v0.6 established immutable scientific structure identity, VASP Calculation identity,
ExecutionAttempt/RemoteJob separation, raw result Artifact intake, Analysis-produced derived Artifacts,
scientific provenance/freshness, exact atom-UID recovery, and durable multi-Calculation workflow
orchestration.

v0.7 adds electronic-structure analysis without weakening those boundaries. The existing domain already
contains `DOS_STATIC`, `CHARGE_STATIC`, and `LOBSTER_PREREQUISITE` Calculation types; `DOS`, `PDOS`,
`BADER`, `CHARGE_DIFFERENCE`, `COHP`, and `BAND_CENTER` Analysis types; and CHGCAR/AECCAR/DOSCAR,
ACF.dat, and COHPCAR Artifact types. The missing layer is a canonical electronic-analysis result model,
external-tool provenance, and deterministic analysis integration.

## Decision

### 1. Domain boundary

A VASP `Calculation` remains the scientific intent that prepares electronic-structure outputs. It does
not become a DOS/Bader/COHP interpretation object. `ExecutionAttempt` and `RemoteJob` remain VASP run
history. Electronic analysis is represented by `Analysis` consuming immutable Artifacts and producing
new Artifacts through `AnalysisProducerRef`.

The core direction is therefore:

`Calculation -> ExecutionAttempt -> raw Artifact -> Analysis -> normalized/derived Artifact`.

No scheduler success is promoted into a scientific-analysis verdict.

### 2. Raw Artifact, parsed facts, Analysis, and derived descriptor layering

The layers are distinct:

1. **Raw Artifact**: exact engine/external-tool files such as DOSCAR, CHGCAR, AECCAR0/2, WAVECAR,
   ACF.dat, COHPCAR.lobster, and ICOHPLIST.lobster. Content SHA-256 is authoritative.
2. **Parsed facts**: normalized numerical data that preserves source semantics without scientific
   interpretation. Examples are native-axis DOS/PDOS arrays, Bader electron populations, native-sign
   COHP curves, and native ICOHP values.
3. **Analysis**: immutable scientific operation identity containing input Artifact IDs, tool/version,
   and a parameters hash. It produces parsed or derived Artifacts through `AnalysisProducerRef`.
4. **Derived descriptor**: an explicitly parameterized scientific reduction such as an element
   aggregation or band/d-band/p-band center. It is never back-written into raw parsed facts.

Parsed-fact normalization may be implemented by an `Analysis`; this does not make the normalized
numbers an interpretation.

### 3. Canonical DOS/PDOS result contract

Block 1 introduces `CanonicalDosResult` as the normalized parsed-fact contract. It contains:

- one immutable `StructureSnapshotId`;
- the exact frozen atom-index-map SHA-256 used for site projection mapping;
- one strictly increasing energy grid in eV;
- the Fermi level in the same reference frame;
- system DOS and optional atom/orbital projected series;
- explicit collinear spin channels;
- a contract version and deterministic content hash.

Every series must use the identical energy grid. Canonical parsed data rejects element aggregation;
element-resolved DOS is a downstream deterministic aggregation from exact atom-resolved projections.

### 4. atom_uid mapping for projected DOS

VASP site indices are not scientific atom identities. A PDOS parser must consume the frozen
`atom-index-map.json` belonging to the exact staged Calculation input and map every projected site to
its permanent `atom_uid`. The normalized dataset records the map content hash and source
`StructureSnapshotId`.

Geometric nearest-neighbour matching is forbidden. Missing, stale, mismatched, duplicate, or
out-of-range index mappings fail closed.

### 5. Element, orbital, and spin projection semantics

`ProjectionScope` distinguishes system, atom, and element scopes. Canonical raw PDOS contains system
and atom scopes only. Element scope is reserved for explicit derived aggregation.

`OrbitalChannel` records a normalized orbital label plus angular-momentum quantum number without
hard-coding one parser's DOSCAR column order. Spin is explicit as `TOTAL`, `UP`, or `DOWN`. A canonical
dataset must use exactly one non-spin `TOTAL` system channel or one collinear `UP/DOWN` pair, and every
atom/orbital projection must use the same spin schema.

Noncollinear/SOC magnetization-resolved DOS is outside the initial v0.7 contract and must fail closed
rather than being mislabeled as collinear spin.

### 6. Fermi level and energy-zero alignment

Canonical parsed DOS retains the **native VASP energy axis**. It stores `E_F` separately in that same
frame. Parsing must not silently replace energies by `E-E_F`.

`E-E_F` is an explicit view/transformation. Any cross-calculation alignment other than native VASP or
explicit Fermi-relative display is a separate parameterized analysis. Vacuum alignment requires an
appropriate electrostatic-potential source and is deferred until a dedicated contract exists.

### 7. DOS Calculation versus DOS Analysis

`DOS_STATIC` remains the VASP Calculation that determines method, structure, k mesh, smearing,
`NEDOS`, `LORBIT`, and produced raw files. `DOS`/`PDOS` Analyses consume those exact outputs.

Changing VASP scientific inputs creates a new Calculation identity. Changing only an analysis selector,
aggregation, energy window, or descriptor definition creates a new Analysis identity while retaining
the upstream Calculation.

### 8. Charge-density Artifact boundary

CHGCAR, AECCAR0/1/2, and future supported charge/potential files remain raw Artifacts. Large volumetric
arrays are not embedded into ProjectStore entity rows. Retrieval may remain on-demand under the
existing Artifact lifecycle.

A normalized or subtracted volumetric result is a derived Artifact produced by an Analysis. Block 1
does not introduce a second large-data database.

### 9. Bader external-tool intake and provenance

Bader is an external Analysis, not a VASP Calculation and not an ExecutionAttempt. A Bader Analysis
consumes exact charge-density Artifacts and records:

- tool name and version;
- argv-style command provenance, not an opaque shell string;
- logical input roles and exact input content SHA-256 values;
- the Analysis parameters hash;
- raw external result Artifacts such as ACF.dat when retained;
- normalized parsed Bader facts as an Analysis-produced Artifact.

Credentials, hostnames, and scheduler/runtime identity are not part of scientific Bader identity.

### 10. Charge-density-difference dependency DAG

The v0.3 frozen triplet remains authoritative: combined system, frozen slab fragment, and frozen
adsorbate fragment are separate `CHARGE_STATIC` Calculations. The downstream
`CHARGE_DIFFERENCE` Analysis consumes all three exact charge-density Artifacts and validates equal
lattice/periodic semantics plus exact output-grid compatibility before subtraction.

The scientific DAG records all three inputs. Mutation or supersession of any member propagates
staleness through existing `SCIENTIFIC` dependencies. The derived field is defined explicitly as
`rho_combined - rho_slab - rho_adsorbate`; no fragment is inferred by geometry.

### 11. LOBSTER prerequisite versus external analysis

`LOBSTER_PREREQUISITE` remains a VASP Calculation whose responsibility is a suitable wavefunction and
fingerprinted VASP inputs. LOBSTER itself is external analysis.

v0.7 initially supports **LOBSTER result intake**, not a new scheduler backend or a second generic
execution model. Imported LOBSTER results must be bound to the exact prerequisite Calculation and
input Artifact hashes, with known LOBSTER version/command provenance when available. Licensed POTCAR
bodies remain outside persisted project data.

### 12. COHP/ICOHP facts versus interpretation

Parsed COHP energy grids, pair labels/identities, raw COHP values, and ICOHP values are facts. The
native LOBSTER sign convention is preserved in the canonical parsed result. Plotting transformations
such as `-COHP`, bonding/antibonding labels, threshold classifications, and qualitative chemical
interpretation are downstream views/analyses and may not rewrite raw facts.

Pair identity must ultimately bind to permanent atom identities where the source data and exact
structure mapping permit it; geometry guessing is forbidden.

### 13. Derived band-center scientific identity

Band center, d-band center, and p-band center are `BAND_CENTER` Analyses. Their scientific identity
must include at least:

- exact upstream DOS/PDOS Artifact content hash;
- atom/element/orbital/spin selector;
- energy reference/alignment semantics;
- integration window;
- numerical integration rule and normalization convention;
- implementation/tool version and parameters hash.

Changing any of these produces a new Analysis identity. A descriptor is not a property silently stored
on `StructureSnapshot` or `Calculation`.

### 14. Freshness propagation

v0.7 reuses the existing `FreshnessEngine` and `DependencyKind.SCIENTIFIC`. Electronic Analyses and
Analysis-produced Artifacts receive scientific dependency records to every scientific upstream input
that defines their result.

A changed raw Artifact hash, changed Calculation scientific identity, changed accepted structure,
changed atom map, changed external-tool parameters, or superseded upstream result makes downstream
analysis stale through the existing DAG. No electronic-analysis-specific second freshness engine is
introduced.

### 15. Workflow integration and v0.6 reuse

The persisted v0.6 `ScientificWorkflowPlan` intentionally models Calculation steps only. v0.7 does not
silently widen `WorkflowStepSpec` or `WorkflowStepBinding` to Analysis and does not introduce a second
persisted workflow state machine.

Electronic-analysis readiness is derived from the current accepted Calculation/artifact state and the
scientific provenance DAG. Later v0.7 blocks may add a pure reconciliation/projection layer that says
which Analysis is ready, blocked, fresh, stale, or complete. Existing v0.6 structure promotion,
continuation, and Calculation generation semantics remain unchanged.

A future need for persisted mixed Calculation/Analysis workflow steps would require a separate ADR and
explicit schema review.

### 16. External-tool version, command, and input provenance

Block 1 introduces `ExternalToolInvocation` and `ExternalInputDigest`. The provenance hash covers tool,
tool version, ordered argv, logical input roles, and exact input content hashes. This value contract is
scheduler-independent and reusable for Bader and LOBSTER intake.

Analysis records continue to carry `tool`, `tool_version`, and `parameters_hash`; detailed invocation
provenance is materialized as analysis metadata/derived data in later blocks rather than changing the
frozen `Analysis` entity in Block 1.

### 17. Schema migration decision

No schema migration is required for Block 1 or the planned file-backed normalized analysis datasets.
`SCHEMA_VERSION` remains 3. The existing generic Artifact/Analysis/provenance persistence model is
sufficient.

A schema v4 is allowed only if a later block demonstrates a durable entity requirement that cannot be
expressed safely with the existing model. Such a change requires a new ADR and explicit v3->v4
migration; it must not be smuggled into parser work.

The package development version advances to `0.7.0.dev0` without creating a tag, Release, or PyPI
publication.

### 18. Explicitly deferred analysis

v0.7 does not implement:

- ZPE, entropy, vibrational thermochemistry, thermal corrections;
- gas reference-energy management, CHE, ORR/OER/HER/CO2RR free-energy diagrams;
- potential or pH corrections;
- noncollinear/SOC spin decomposition;
- vacuum-level alignment or work-function analysis without a dedicated potential contract;
- band-structure path analysis, Wannier analysis, ELF interpretation, COOP/COBI, or generic
  wavefunction visualization;
- GUI work, a new scheduler backend, or a large workflow/database framework.

### 19. v0.7 block plan

1. **Electronic Analysis Domain Contracts** — canonical energy/spin/projection contracts,
   atom-map binding digest, external-tool invocation provenance, ADR-048.
2. **DOS/PDOS Canonical Intake** — DOSCAR/approved source parsing, exact frozen atom-map mapping,
   fail-closed spin/orbital normalization.
3. **Durable DOS/PDOS Analysis Materialization** — Analysis/Artifact/provenance creation, deterministic
   serialization, reopen, and freshness.
4. **Bader Intake and Charge Partition Provenance** — ACF intake, exact charge/reference dependencies,
   normalized Bader facts, external-tool provenance.
5. **Charge-Density Difference Analysis** — strict triplet result validation, FFT-grid equality,
   subtraction, durable volumetric metadata/result provenance.
6. **LOBSTER / COHP / ICOHP Result Intake** — prerequisite binding, external result provenance,
   canonical native-sign COHP and ICOHP facts.
7. **Electronic Descriptors** — atom/element/orbital aggregation and parameterized band/d-band/p-band
   center calculations.
8. **Electronic Analysis Reconciliation and Workflow Integration** — pure readiness/freshness
   projection on top of v0.6 + durable reopen/idempotency hardening without a second workflow state
   machine.
9. **Final v0.7 E2E Acceptance and Hardening** — cross-layer adversarial tests and final scope lock.

### 20. Final E2E acceptance criteria

v0.7 is complete only when tests demonstrate all of the following:

- a verified `DOS_STATIC` result can produce canonical total DOS and permanent-`atom_uid` PDOS without
  geometric identity guessing;
- native energies and Fermi level survive serialize/reopen exactly and no implicit zero shift occurs;
- analysis provenance records exact input Artifact hashes, parameters, and tool version;
- scientific input/artifact drift marks electronic results and downstream descriptors stale;
- Bader intake is reproducible from exact charge-density inputs and external-tool provenance;
- charge-density difference refuses incompatible structures or FFT grids and records all three
  scientific parents;
- LOBSTER results cannot bind to the wrong prerequisite Calculation and COHP/ICOHP native facts remain
  separate from sign-transformed interpretation;
- band-center identity changes when selector, spin, orbital, window, alignment, integration rule, or
  upstream PDOS changes;
- ProjectStore reopen preserves the full Analysis/Artifact/provenance chain;
- v0.6 Calculation workflow acceptance remains valid and electronic-analysis reconciliation does not
  mutate historical workflow generations;
- Ruff, mypy strict, pytest on Python 3.11/3.12/3.13, and MatterViz contract all remain green.

## Consequences

v0.7 extends the scientific analysis surface while preserving every frozen v0.1-v0.6 identity,
provenance, convergence, promotion, and workflow boundary. The architecture is deliberately
file-backed and dependency-DAG-driven; no new runtime dependency or schema migration is justified by
Block 1.
