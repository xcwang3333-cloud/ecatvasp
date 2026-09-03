# ADR-017 — v0.3 Analysis Prerequisite Contracts

- Status: Accepted
- Date: 2026-09-03
- Scope: v0.3 Block 9

## Context

ECatVASP distinguishes electronic-structure calculations from analyses derived from their outputs. DOS/PDOS, charge-density difference, and COHP are analyses, but each requires a deliberately prepared VASP calculation whose outputs are suitable for the downstream analysis.

The existing frozen domain already provides `DOS_STATIC`, `CHARGE_STATIC`, and `LOBSTER_PREREQUISITE` Calculation types and `DOS`, `PDOS`, `CHARGE_DIFFERENCE`, and `COHP` Analysis types. Block 9 therefore must add preparation contracts without introducing new domain entities or changing storage schema version 1.

## Decision

### Calculation versus Analysis boundary

- `DOSPrerequisite`, `ChargeDensityStatic`, and `LobsterPrerequisite` remain VASP Calculations.
- DOS/PDOS parsing, charge-density subtraction, and COHP interpretation remain Analyses consuming produced Artifacts.
- Block 9 does not parse DOSCAR/CHGCAR/WAVECAR/LOBSTER outputs and does not create Analysis results.

### DOS / PDOS prerequisite

`DOSPrerequisite` is a static VASP calculation using the already fingerprinted Method, Protocol, k-point policy, and project numerical lock.

The recipe requires explicit `NEDOS >= 2`. ECatVASP fixes `LORBIT=11` for the v0.3 DOS/PDOS prerequisite so site- and lm-resolved projections are available for downstream PDOS analysis. It does not silently replace the Protocol smearing, SIGMA, ENCUT, or k-point policy.

The ionic/static recipe layer is:

- `IBRION=-1`
- `NSW=0`
- `LORBIT=11`
- explicit fingerprinted `NEDOS`
- `LCHARG=.FALSE.`
- `LWAVE=.FALSE.`

### Charge-density prerequisite and strict triplet

A charge-density difference analysis is based on three separate `ChargeDensityStatic` Calculations:

1. combined adsorbate + slab;
2. frozen slab fragment;
3. frozen adsorbate fragment.

Each member uses:

- `IBRION=-1`
- `NSW=0`
- `LCHARG=.TRUE.`
- `LWAVE=.FALSE.`
- `LAECHG=.FALSE.`

The triplet fails closed unless:

- all three Calculations belong to one Project and use `ChargeDensityStatic`;
- the lattice and periodic semantics are identical;
- slab and adsorbate atom-UID sets are disjoint and their union exactly equals the combined atom-UID set;
- every retained atom preserves the exact element and fractional coordinates from the combined snapshot;
- non-element-specific Method settings are identical;
- each shared element uses the same POTCAR identity and the same DFT+U setting;
- numerical/electronic Protocol settings are identical;
- for spin-polarized calculations, fragment MAGMOM maps are exact frozen subsets of the combined UID-addressed mapping;
- v0.3 triplet members are neutral.

The three core Method hashes are not required to be identical because each fragment Method must contain only the POTCAR identities for species actually present in that fragment. This is compatible with the existing strict local POTCAR resolver.

All three members are fully preflighted before any member is materialized, reducing the risk of a partially generated triplet.

Input compatibility does not prove that completed CHGCAR files share the same FFT grid. Exact output-grid compatibility remains a downstream `CHARGE_DIFFERENCE` Analysis validation requirement.

### LOBSTER prerequisite

`LobsterPrerequisite` prepares a static VASP wavefunction suitable for later LOBSTER execution. Block 9 does not execute LOBSTER and does not generate `lobsterin`.

The recipe requires an explicit positive `NBANDS`; ECatVASP does not guess a band count. The Protocol must explicitly fingerprint `ISYM=0`.

The ionic/static recipe layer is:

- `IBRION=-1`
- `NSW=0`
- explicit fingerprinted `NBANDS`
- `ISYM=0` from Protocol
- `LWAVE=.TRUE.`
- `LCHARG=.FALSE.`

The licensed POTCAR boundary remains unchanged: POTCAR bodies are resolved and verified locally but are never committed, packaged, persisted, or materialized into the project.

## Consequences

- No frozen Domain or storage-schema migration is introduced.
- Downstream Analyses have explicit, reproducible prerequisite Calculation identities.
- Charge-density subtraction cannot silently mix relaxed fragments, changed coordinates, incompatible numerical settings, or inconsistent shared-element methods.
- LOBSTER preparation is deterministic without embedding version-specific basis-selection or LOBSTER execution policy into the VASP layer.
- Block 10 remains responsible for global fail-closed reconciliation/import roundtrips; Block 11 remains responsible for ExecutionPlan acceptance.
