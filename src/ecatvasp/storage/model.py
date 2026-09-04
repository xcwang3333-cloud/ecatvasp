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
    ScientificWorkflowPlan,
    StateConformer,
    StructureSnapshot,
    StructureVariant,
    WorkflowStepBinding,
    validate_conformer_context,
)
from ecatvasp.domain.calculation import (
    AnalysisProducerRef,
    CalculationProducerRef,
    ExecutionAttemptProducerRef,
)
from ecatvasp.domain.calculation_validation import validate_attempt_history
from ecatvasp.provenance import (
    DependencyGraph,
    DependencyRecord,
    ProvenanceIntegrityError,
    ProvenanceRecord,
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
    workflow_plans: tuple[ScientificWorkflowPlan, ...] = ()
    calculations: tuple[Calculation, ...] = ()
    workflow_step_bindings: tuple[WorkflowStepBinding, ...] = ()
    execution_attempts: tuple[ExecutionAttempt, ...] = ()
    remote_jobs: tuple[RemoteJob, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    analyses: tuple[Analysis, ...] = ()
    provenance_records: tuple[ProvenanceRecord, ...] = ()
    dependency_records: tuple[DependencyRecord, ...] = ()

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
            *self.workflow_plans,
            *self.calculations,
            *self.workflow_step_bindings,
            *self.execution_attempts,
            *self.remote_jobs,
            *self.artifacts,
            *self.analyses,
            *self.provenance_records,
            *self.dependency_records,
        )

    def provenance_entities(self) -> tuple[object, ...]:
        """Return scientific entities that may participate in provenance relationships.

        Workflow plans and bindings are intentionally excluded: they describe orchestration identity
        and may reference scientific entities, but they do not themselves produce scientific facts.
        """

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
        workflow_plans = {item.id: item for item in self.workflow_plans}
        calculations = {item.id: item for item in self.calculations}
        workflow_bindings = {item.id: item for item in self.workflow_step_bindings}
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

        for structure_snapshot in self.structure_snapshots:
            parent_snapshot_id = structure_snapshot.parent_snapshot_id
            if parent_snapshot_id is not None and parent_snapshot_id not in snapshots:
                raise ProjectIntegrityError("StructureSnapshot parent is missing")

        for active_site in self.active_sites:
            if active_site.structure_variant_id not in variants:
                raise ProjectIntegrityError("ActiveSite references a missing StructureVariant")

        for adsorption_state in self.adsorption_states:
            if adsorption_state.structure_variant_id not in variants:
                raise ProjectIntegrityError("AdsorptionState references a missing StructureVariant")
            active_site_id = adsorption_state.active_site_id
            if active_site_id is not None and active_site_id not in active_sites:
                raise ProjectIntegrityError("AdsorptionState references a missing ActiveSite")

        for conformer in self.state_conformers:
            conformer_state = states.get(conformer.adsorption_state_id)
            conformer_snapshot = snapshots.get(conformer.structure_snapshot_id)
            if conformer_state is None or conformer_snapshot is None:
                raise ProjectIntegrityError("StateConformer references a missing state or snapshot")
            parent_conformer_id = conformer.parent_conformer_id
            if parent_conformer_id is not None and parent_conformer_id not in conformers:
                raise ProjectIntegrityError("StateConformer parent is missing")
            if conformer_state.active_site_id is None:
                if conformer.binding_edges:
                    raise ProjectIntegrityError(
                        "binding edges require an AdsorptionState ActiveSite"
                    )
            else:
                active_site = active_sites[conformer_state.active_site_id]
                try:
                    validate_conformer_context(
                        active_site=active_site,
                        state=conformer_state,
                        conformer=conformer,
                        snapshot=conformer_snapshot,
                    )
                except ValueError as error:
                    raise ProjectIntegrityError(str(error)) from error

        for workflow_plan in self.workflow_plans:
            if workflow_plan.project_id != self.project.id:
                raise ProjectIntegrityError("ScientificWorkflowPlan belongs to a different Project")
            if workflow_plan.root_structure_snapshot_id not in snapshots:
                raise ProjectIntegrityError(
                    "ScientificWorkflowPlan root StructureSnapshot is missing"
                )

        for calculation in self.calculations:
            if calculation.project_id != self.project.id:
                raise ProjectIntegrityError("Calculation belongs to a different Project")
            if calculation.input_structure_snapshot_id not in snapshots:
                raise ProjectIntegrityError("Calculation input StructureSnapshot is missing")
            if calculation.method_fingerprint_id not in methods:
                raise ProjectIntegrityError("Calculation MethodFingerprint is missing")

        binding_keys = tuple(
            (item.workflow_plan_id, item.step_key, item.generation)
            for item in self.workflow_step_bindings
        )
        if len(binding_keys) != len(set(binding_keys)):
            raise ProjectIntegrityError(
                "workflow plan/step/generation bindings must be unique"
            )
        superseded_binding_ids = tuple(
            item.supersedes_binding_id
            for item in self.workflow_step_bindings
            if item.supersedes_binding_id is not None
        )
        if len(superseded_binding_ids) != len(set(superseded_binding_ids)):
            raise ProjectIntegrityError("a WorkflowStepBinding may be superseded only once")

        for binding in self.workflow_step_bindings:
            plan = workflow_plans.get(binding.workflow_plan_id)
            if plan is None:
                raise ProjectIntegrityError(
                    "WorkflowStepBinding references a missing workflow plan"
                )
            try:
                step = plan.step(binding.step_key)
            except KeyError as error:
                raise ProjectIntegrityError(
                    "WorkflowStepBinding references a missing workflow step"
                ) from error
            calculation = calculations.get(binding.calculation_id)
            if calculation is None:
                raise ProjectIntegrityError("WorkflowStepBinding references a missing Calculation")
            if binding.resolved_input_structure_snapshot_id not in snapshots:
                raise ProjectIntegrityError(
                    "WorkflowStepBinding resolved StructureSnapshot is missing"
                )
            if (
                calculation.input_structure_snapshot_id
                != binding.resolved_input_structure_snapshot_id
            ):
                raise ProjectIntegrityError(
                    "WorkflowStepBinding resolved snapshot does not match Calculation input"
                )
            if calculation.calculation_type is not step.calculation_type:
                raise ProjectIntegrityError(
                    "WorkflowStepBinding CalculationType does not match workflow step"
                )
            if calculation.recipe_id != step.recipe_id:
                raise ProjectIntegrityError(
                    "WorkflowStepBinding Calculation recipe does not match workflow step"
                )
            if binding.supersedes_binding_id is not None:
                previous = workflow_bindings.get(binding.supersedes_binding_id)
                if previous is None:
                    raise ProjectIntegrityError(
                        "WorkflowStepBinding supersedes a missing binding"
                    )
                if (
                    previous.workflow_plan_id != binding.workflow_plan_id
                    or previous.step_key != binding.step_key
                ):
                    raise ProjectIntegrityError(
                        "WorkflowStepBinding may supersede only the same workflow step"
                    )
                if previous.generation + 1 != binding.generation:
                    raise ProjectIntegrityError(
                        "WorkflowStepBinding generations must form a contiguous chain"
                    )

        for attempt in self.execution_attempts:
            if attempt.calculation_id not in calculations:
                raise ProjectIntegrityError("ExecutionAttempt references a missing Calculation")

        for calculation in self.calculations:
            calculation_attempts = tuple(
                attempt
                for attempt in self.execution_attempts
                if attempt.calculation_id == calculation.id
            )
            try:
                validate_attempt_history(
                    calculation=calculation,
                    attempts=calculation_attempts,
                )
            except ValueError as error:
                raise ProjectIntegrityError(str(error)) from error

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

        provenance_entity_ids = {_entity_uuid(entity) for entity in self.provenance_entities()}
        provenance_subjects: set[UUID] = set()
        for provenance in self.provenance_records:
            if provenance.subject_id not in provenance_entity_ids:
                raise ProjectIntegrityError("ProvenanceRecord subject is missing")
            method_id = provenance.method_fingerprint_id
            if method_id is not None and method_id not in methods:
                raise ProjectIntegrityError("ProvenanceRecord MethodFingerprint is missing")
            provenance_subjects.add(provenance.subject_id)

        for dependency in self.dependency_records:
            if dependency.upstream_id not in provenance_entity_ids:
                raise ProjectIntegrityError("DependencyRecord upstream entity is missing")
            if dependency.downstream_id not in provenance_entity_ids:
                raise ProjectIntegrityError("DependencyRecord downstream entity is missing")
            if dependency.downstream_id not in provenance_subjects:
                raise ProjectIntegrityError(
                    "DependencyRecord downstream entity requires a ProvenanceRecord"
                )
        try:
            DependencyGraph(self.dependency_records)
        except ProvenanceIntegrityError as error:
            raise ProjectIntegrityError(str(error)) from error

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
            workflow_plans=tuple(
                item for item in entities if isinstance(item, ScientificWorkflowPlan)
            ),
            calculations=tuple(item for item in entities if isinstance(item, Calculation)),
            workflow_step_bindings=tuple(
                item for item in entities if isinstance(item, WorkflowStepBinding)
            ),
            execution_attempts=tuple(
                item for item in entities if isinstance(item, ExecutionAttempt)
            ),
            remote_jobs=tuple(item for item in entities if isinstance(item, RemoteJob)),
            artifacts=tuple(item for item in entities if isinstance(item, Artifact)),
            analyses=tuple(item for item in entities if isinstance(item, Analysis)),
            provenance_records=tuple(
                item for item in entities if isinstance(item, ProvenanceRecord)
            ),
            dependency_records=tuple(
                item for item in entities if isinstance(item, DependencyRecord)
            ),
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
