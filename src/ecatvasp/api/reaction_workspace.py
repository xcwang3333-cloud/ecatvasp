"""Public v1.1 Block 7 reaction workspace with durable CHE-reference lineage.

The scientific reaction implementation lives in ``_reaction_workspace_core`` and remains a thin
composition over the frozen v0.8 thermochemistry/CHE/reaction/descriptor/diagram authorities. This
wrapper only hardens generic Analysis/Dependency provenance so a molecular H2 reference used to
construct a CHE source remains an explicit durable scientific input even when H2 is not a direct
stoichiometric pathway source.
"""

from __future__ import annotations

from dataclasses import replace

from ecatvasp.api._reaction_workspace_core import (
    CO2RRToCOPresetAnalysisBindings,
    HERPresetAnalysisBindings,
    OERPresetAnalysisBindings,
    ORRPresetAnalysisBindings,
    PreparedReactionWorkspace,
    ReactionPresetAnalysisBindings,
)
from ecatvasp.api._reaction_workspace_core import (
    ProjectReactionWorkspaceApplicationService as _CoreReactionWorkspaceApplicationService,
)
from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.thermochemistry_workspace_support import (
    persist_analysis_graph,
    require_analysis,
    require_artifact,
)
from ecatvasp.domain import Analysis, AnalysisId, ArtifactId
from ecatvasp.provenance import DependencyKind, DependencyRecord, scientific_hash
from ecatvasp.thermo import (
    CHEConditions,
    ElectrocatalysisPresetKind,
    GasReferenceSpecies,
    ReactionSourceArtifactBinding,
    materialize_reaction_diagram,
)

_CHE_H2 = "CHE_H2"


class ProjectReactionWorkspaceApplicationService(
    _CoreReactionWorkspaceApplicationService
):
    """Reaction workspace that preserves the exact durable H2 lineage behind CHE."""

    def materialize_preset_diagram(
        self,
        *,
        preset_kind: ElectrocatalysisPresetKind,
        bindings: ReactionPresetAnalysisBindings,
        baseline_conditions: CHEConditions,
        requested_conditions: CHEConditions,
    ) -> dict[str, object]:
        prepared = self._prepare(
            preset_kind=preset_kind,
            bindings=bindings,
            baseline_conditions=baseline_conditions,
            requested_conditions=requested_conditions,
        )
        che_bindings = self._che_reference_bindings(
            preset_kind=preset_kind,
            bindings=bindings,
        )
        durable_inputs = (*prepared.bindings, *che_bindings)
        expected_input_artifact_ids = tuple(
            sorted({item.artifact.id for item in durable_inputs}, key=str)
        )

        existing = self._find_existing_diagram(prepared)
        if existing is not None:
            analysis, artifact = existing
            if analysis.input_artifact_ids != expected_input_artifact_ids:
                raise ApplicationServiceError(
                    "existing reaction diagram lacks the exact durable CHE reference lineage"
                )
            self._require_che_dependencies(analysis.id, che_bindings)
            return {
                "analysis_id": str(analysis.id),
                "artifact_id": str(artifact.id),
                "preset_kind": preset_kind.value,
                "reused": True,
            }

        durable = materialize_reaction_diagram(
            project_root=self.store.root,
            definition=prepared.preset.definition,
            sources=prepared.sources,
            source_bindings=prepared.bindings,
            baseline_conditions=prepared.baseline_conditions,
            requested_conditions=prepared.requested_conditions,
            descriptor_definitions=prepared.descriptors,
        )
        analysis = replace(
            durable.analysis,
            input_artifact_ids=expected_input_artifact_ids,
        )
        dependencies = _reaction_dependencies_with_che_lineage(
            dependency_records=durable.dependency_records,
            original_analysis=durable.analysis,
            analysis=analysis,
            output_artifact_id=durable.artifact.id,
            che_bindings=che_bindings,
        )

        current = self.store.open()
        for binding in durable_inputs:
            if require_analysis(current, binding.analysis.id) != binding.analysis:
                raise ApplicationServiceError(
                    "reaction thermochemistry Analysis changed during diagram materialization"
                )
            if require_artifact(current, binding.artifact.id) != binding.artifact:
                raise ApplicationServiceError(
                    "reaction thermochemistry Artifact changed during diagram materialization"
                )
        persist_analysis_graph(
            store=self.store,
            bundle=current,
            analysis=analysis,
            artifacts=(durable.artifact,),
            provenance_records=durable.provenance_records,
            dependency_records=dependencies,
        )
        return {
            "analysis_id": str(analysis.id),
            "artifact_id": str(durable.artifact.id),
            "preset_kind": preset_kind.value,
            "dataset_hash": durable.dataset.result_hash,
            "reused": False,
        }

    def _che_reference_bindings(
        self,
        *,
        preset_kind: ElectrocatalysisPresetKind,
        bindings: ReactionPresetAnalysisBindings,
    ) -> tuple[ReactionSourceArtifactBinding, ...]:
        if preset_kind is ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY:
            if not isinstance(bindings, HERPresetAnalysisBindings):
                raise ApplicationServiceError("HER preset requires HER analysis bindings")
            return ()
        if preset_kind is ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E:
            if not isinstance(bindings, ORRPresetAnalysisBindings):
                raise ApplicationServiceError("ORR preset requires ORR analysis bindings")
            analysis_id = bindings.h2_reference_analysis_id
        elif preset_kind is ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E:
            if not isinstance(bindings, OERPresetAnalysisBindings):
                raise ApplicationServiceError("OER preset requires OER analysis bindings")
            analysis_id = bindings.h2_reference_analysis_id
        elif preset_kind is ElectrocatalysisPresetKind.CO2RR_TO_CO_2E:
            if not isinstance(bindings, CO2RRToCOPresetAnalysisBindings):
                raise ApplicationServiceError(
                    "CO2RR-to-CO preset requires CO2RR analysis bindings"
                )
            analysis_id = bindings.h2_reference_analysis_id
        else:
            raise ApplicationServiceError("unsupported electrocatalysis preset kind")
        h2 = self._molecular(
            analysis_id,
            _CHE_H2,
            GasReferenceSpecies.H2,
        )
        return (h2.binding,)

    def _require_che_dependencies(
        self,
        analysis_id: AnalysisId,
        che_bindings: tuple[ReactionSourceArtifactBinding, ...],
    ) -> None:
        if not che_bindings:
            return
        bundle = self.store.open()
        for binding in che_bindings:
            expected = (
                (
                    binding.analysis.id,
                    f"reaction_che_reference_analysis:{binding.species_key}",
                    scientific_hash(binding.analysis),
                ),
                (
                    binding.artifact.id,
                    f"reaction_che_reference_artifact:{binding.species_key}",
                    scientific_hash(binding.artifact),
                ),
            )
            for upstream_id, role, recorded_hash in expected:
                if not any(
                    item.kind is DependencyKind.SCIENTIFIC
                    and item.upstream_id == upstream_id
                    and item.downstream_id == analysis_id
                    and item.role == role
                    and item.recorded_hash == recorded_hash
                    for item in bundle.dependency_records
                ):
                    raise ApplicationServiceError(
                        "existing reaction diagram lacks exact CHE reference dependencies"
                    )


def _reaction_dependencies_with_che_lineage(
    *,
    dependency_records: tuple[DependencyRecord, ...],
    original_analysis: Analysis,
    analysis: Analysis,
    output_artifact_id: ArtifactId,
    che_bindings: tuple[ReactionSourceArtifactBinding, ...],
) -> tuple[DependencyRecord, ...]:
    analysis_hash = scientific_hash(analysis)
    records: list[DependencyRecord] = []
    for item in dependency_records:
        if (
            item.upstream_id == original_analysis.id
            and item.downstream_id == output_artifact_id
        ):
            records.append(replace(item, recorded_hash=analysis_hash))
        else:
            records.append(item)
    for binding in che_bindings:
        records.extend(
            (
                DependencyRecord(
                    upstream_id=binding.analysis.id,
                    downstream_id=analysis.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"reaction_che_reference_analysis:{binding.species_key}",
                    recorded_hash=scientific_hash(binding.analysis),
                ),
                DependencyRecord(
                    upstream_id=binding.artifact.id,
                    downstream_id=analysis.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"reaction_che_reference_artifact:{binding.species_key}",
                    recorded_hash=scientific_hash(binding.artifact),
                ),
            )
        )
    return tuple(records)


__all__ = [
    "CO2RRToCOPresetAnalysisBindings",
    "HERPresetAnalysisBindings",
    "OERPresetAnalysisBindings",
    "ORRPresetAnalysisBindings",
    "PreparedReactionWorkspace",
    "ProjectReactionWorkspaceApplicationService",
    "ReactionPresetAnalysisBindings",
]
