"""Project-level storage aggregate and graph-integrity validation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    Analysis,
    Artifact,
    Calculation,
    Catalyst,
    ExecutionAttempt,
    MethodFingerprint,
    Project,
    RemoteJob,
    StateConformer,
    StructureSnapshot,
    StructureVariant,
    validate_conformer_context,
)
from ecatvasp.domain.calculation import (
    AnalysisProducerRef,
    CalculationProducerRef,
    ExecutionAttemptProducerRef,
)


class ProjectIntegrityError(ValueError):
    """Raised when individually valid entities form an inconsistent project graph."""


@dataclass(frozen=True, slots=True)
class ProjectBundle:
    """Self-contained in-memory aggregate persisted by one project store."""

    project: Project
    catalysts: tuple[Catalyst, ...] = ()
    structure_variants: tuple[StructureVariant, ...] = ()
    structure_snapshots: tuple[StructureSnapshot, ...] = ()
    active_sites: tuple[ActiveSite, ...] = ()
    adsorption_states: tuple[AdsorptionState, ...] = ()
    state_conformers: tuple[StateConformer, ...] = ()
    method_fingerprints: tuple[MethodFingerprint, ...] = ()
    calculations: tuple[Calculation, ...] = ()
    execution_attempts: tuple[ExecutionAttempt, ...] = ()
    remote_jobs: tuple[RemoteJob, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    analyses: tuple[Analysis, ...] = ()

    def entities(self) -> tuple[object, ...]:
        """Return all persisted entities in deterministic group order."""

        return (
            self.project,
            *self.catalysts,
            *self.structure_variants,
            *self.structure_snapshots,
            *self.active_sites,
            *self.adsorption_states,
            *self.state_conformers,
            *self.method_fingerprints,
            *self.calculations,
            *self.execution_attempts,
            *self.remote_jobs,
            *self.artifacts,
            *self.analyses,
        )

    def validate(self) -> None:
        """Validate referential integrity before a project is persisted."""

        entities = self.entities()
        entity_ids = [_entity_uuid(entity) for entity in entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ProjectIntegrityError("entity UUIDs must be globally unique within a project")

        catalysts = {item.id: item for item in self.catalysts}
        variants = {item.id: item for item in self.structure_variants}
        snapshots = {item.id: item for item in self.structure_snapshots}
        active_sites = {item.id: item for item in self.active_sites}
        states = {item.id: item for item in self.adsorption_states}
        conformers = {item.id: item for item in self.state_conformers}
        methods = {item.id: item for item in self.method_fingerprints}
        calculations = {item.id: item for item in self.calculations}
        attempts = {item.id: item for item in self.execution_attempts}
        artifacts = {item.id: item for item in self.artifacts}
        analyses = {item.id: item for item in self.analyses}

        for catalyst in self.catalysts:
            if catalyst.project_id != self.project.id:
                raise ProjectIntegrityError("Catalyst belongs to a different Project")

        for variant in self.structure_variants:
            if variant.catalyst_id not in catalysts:
                raise ProjectIntegrityError("StructureVariant references a missing Catalyst")
            if variant.parent_variant_id is not None and variant.parent_variant_id not in variants:
                raise ProjectIntegrityError("StructureVariant parent is missing")
            current = variant.current_structure_snapshot_id
            if current is not None and current not in snapshots:
                raise ProjectIntegrityError("StructureVariant current snapshot is missing")

        for snapshot in self.structure_snapshots:
            parent = snapshot.parent_snapshot_id
            if parent is not None and parent not in snapshots:
                raise ProjectIntegrityError("StructureSnapshot parent is missing")

        for active_site in self.active_sites:
            if active_site.structure_variant_id not in variants:
                raise ProjectIntegrityError("ActiveSite references a missing StructureVariant")

        for state in self.adsorption_states:
            if state.structure_variant_id not in variants:
                raise ProjectIntegrityError("AdsorptionState references a missing StructureVariant")
            if state.active_site_id is not None and state.active_site_id not in active_sites:
                raise ProjectIntegrityError("AdsorptionState references a missing ActiveSite")

        for conformer in self.state_conformers:
            state = states.get(conformer.adsorption_state_id)
            snapshot = snapshots.get(conformer.structure_snapshot_id)
            if state is None or snapshot is None:
                raise ProjectIntegrityError("StateConformer references a missing state or snapshot")
            parent = conformer.parent_conformer_id
            if parent is not None and parent not in conformers:
                raise ProjectIntegrityError("StateConformer parent is missing")
            if state.active_site_id is None:
                if conformer.binding_edges:
                    raise ProjectIntegrityError(
                        "binding edges require an AdsorptionState ActiveSite"
                    )
            else:
                active_site = active_sites[state.active_site_id]
                try:
                    validate_conformer_context(
                        active_site=active_site,
                        state=state,
                        conformer=conformer,
                        snapshot=snapshot,
                    )
                except ValueError as error:
                    raise ProjectIntegrityError(str(error)) from error

        for calculation in self.calculations:
            if calculation.project_id != self.project.id:
                raise ProjectIntegrityError("Calculation belongs to a different Project")
            if calculation.input_structure_snapshot_id not in snapshots:
                raise ProjectIntegrityError("Calculation input StructureSnapshot is missing")
            if calculation.method_fingerprint_id not in methods:
                raise ProjectIntegrityError("Calculation MethodFingerprint is missing")

        for attempt in self.execution_attempts:
            if attempt.calculation_id not in calculations:
                raise ProjectIntegrityError("ExecutionAttempt references a missing Calculation")
            previous = attempt.previous_attempt_id
            if previous is not None and previous not in attempts:
                raise ProjectIntegrityError("ExecutionAttempt previous attempt is missing")

        for remote_job in self.remote_jobs:
            if remote_job.execution_attempt_id not in attempts:
                raise ProjectIntegrityError("RemoteJob references a missing ExecutionAttempt")

        for artifact in self.artifacts:
            producer = artifact.producer
            if isinstance(producer, CalculationProducerRef) and producer.id not in calculations:
                raise ProjectIntegrityError("Artifact Calculation producer is missing")
            if isinstance(producer, ExecutionAttemptProducerRef) and producer.id not in attempts:
                raise ProjectIntegrityError("Artifact ExecutionAttempt producer is missing")
            if isinstance(producer, AnalysisProducerRef) and producer.id not in analyses:
                raise ProjectIntegrityError("Artifact Analysis producer is missing")

        for analysis in self.analyses:
            if analysis.project_id != self.project.id:
                raise ProjectIntegrityError("Analysis belongs to a different Project")
            if any(artifact_id not in artifacts for artifact_id in analysis.input_artifact_ids):
                raise ProjectIntegrityError("Analysis references a missing input Artifact")

    @classmethod
    def from_entities(cls, entities: tuple[object, ...]) -> ProjectBundle:
        """Rebuild a bundle from decoded entity rows."""

        projects = tuple(item for item in entities if isinstance(item, Project))
        if len(projects) != 1:
            raise ProjectIntegrityError("a project store must contain exactly one Project")
        bundle = cls(
            project=projects[0],
            catalysts=tuple(item for item in entities if isinstance(item, Catalyst)),
            structure_variants=tuple(
                item for item in entities if isinstance(item, StructureVariant)
            ),
            structure_snapshots=tuple(
                item for item in entities if isinstance(item, StructureSnapshot)
            ),
            active_sites=tuple(item for item in entities if isinstance(item, ActiveSite)),
            adsorption_states=tuple(
                item for item in entities if isinstance(item, AdsorptionState)
            ),
            state_conformers=tuple(
                item for item in entities if isinstance(item, StateConformer)
            ),
            method_fingerprints=tuple(
                item for item in entities if isinstance(item, MethodFingerprint)
            ),
            calculations=tuple(item for item in entities if isinstance(item, Calculation)),
            execution_attempts=tuple(
                item for item in entities if isinstance(item, ExecutionAttempt)
            ),
            remote_jobs=tuple(item for item in entities if isinstance(item, RemoteJob)),
            artifacts=tuple(item for item in entities if isinstance(item, Artifact)),
            analyses=tuple(item for item in entities if isinstance(item, Analysis)),
        )
        if len(bundle.entities()) != len(entities):
            raise ProjectIntegrityError("project store contains an unsupported entity type")
        bundle.validate()
        return bundle


def _entity_uuid(entity: object) -> UUID:
    value = getattr(entity, "id", None)
    if not isinstance(value, UUID):
        raise ProjectIntegrityError("persisted entities must expose a UUID id")
    return value
