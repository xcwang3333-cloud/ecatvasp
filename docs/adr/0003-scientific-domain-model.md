# ADR-003: Scientific Domain Model

- Status: Accepted
- Date: 2026-09-02

## Context

Electrocatalysis projects require stable identities across catalyst variants, adsorption states, geometry revisions, calculations, and post-processing results. File names and POSCAR indices are insufficient scientific identifiers.

## Decision

The permanent domain model is centered on Project, Catalyst, StructureVariant, StructureSnapshot, ActiveSite, AdsorptionState, StateConformer, Calculation, ExecutionAttempt, RemoteJob, Artifact, Analysis, MethodFingerprint, ReferenceEnergySet, Reaction, ReactionStep, ThermodynamicCondition, ThermochemistryResult, and Figure.

StructureSnapshot objects are immutable. AdsorptionState is a chemical state and is separate from StateConformer geometry. Calculations are separate from execution attempts and from analyses. Permanent entities use stable identifiers; atoms use stable `atom_uid` values rather than POSCAR indices.

## Consequences

Scientific provenance can survive relaxation, file reordering, retries, and multiple adsorption conformers. Later schema changes require explicit migration and ADR review.
