"""Preset-bound electrocatalysis reaction workspace for v1.1 Block 7.

All chemistry is delegated to the frozen v0.8 preset, CHE, reaction, descriptor, and reaction-diagram
engines. The application layer accepts only durable THERMOCHEMISTRY Analysis identities plus explicit
CHE conditions; it never accepts a free-energy table or user-supplied scientific hashes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ecatvasp.api.application import ApplicationServiceError, ProjectApplicationService
from ecatvasp.api.reaction_workspace_support import (
    ResolvedMolecularReactionSource,
    ResolvedSurfaceReactionSource,
    resolve_molecular_reaction_source,
    resolve_surface_reaction_source,
)
from ecatvasp.api.thermochemistry_workspace_support import (
    canonical_analysis_payload,
    persist_analysis_graph,
    require_analysis,
    require_artifact,
)
from ecatvasp.domain import Analysis, AnalysisId, AnalysisStatus, AnalysisType, canonical_json
from ecatvasp.thermo import (
    REACTION_DIAGRAM_TOOL_NAME,
    REACTION_DIAGRAM_TOOL_VERSION,
    CHEConditions,
    CHEHydrogenReference,
    CHEReactionSource,
    ElectrocatalysisPathwayPreset,
    ElectrocatalysisPresetKind,
    GasReferenceSpecies,
    ReactionDiagramDescriptorDefinition,
    ReactionEnergySource,
    ReactionPathwayResult,
    ReactionSourceArtifactBinding,
    ThermochemistrySubjectKind,
    compile_co2rr_to_co_2e_preset,
    compile_her_volmer_heyrovsky_preset,
    compile_oer_associative_4e_preset,
    compile_orr_associative_4e_preset,
    define_her_delta_g_h_star_descriptor,
    define_limiting_potential_descriptor,
    define_oer_overpotential_descriptor,
    define_reversible_potential_descriptor,
    derive_reversible_potential,
    evaluate_her_delta_g_h_star,
    evaluate_oer_theoretical_overpotential,
    evaluate_potential_dependent_pathway_view,
    evaluate_reaction_pathway,
    materialize_reaction_diagram,
    proton_electron_chemical_potential,
    solve_limiting_potential,
)

_CLEAN = "clean_surface"
_H_STAR = "H_star"
_OOH_STAR = "OOH_star"
_O_STAR = "O_star"
_OH_STAR = "OH_star"
_COOH_STAR = "COOH_star"
_CO_STAR = "CO_star"
_H2 = "H2"
_H2O = "H2O"
_O2 = "O2"
_CO2 = "CO2"
_CO = "CO"
_CHE = "H_plus_e"


@dataclass(frozen=True, slots=True)
class HERPresetAnalysisBindings:
    clean_surface_analysis_id: AnalysisId
    h_adsorbed_analysis_id: AnalysisId
    h2_reference_analysis_id: AnalysisId


@dataclass(frozen=True, slots=True)
class ORRPresetAnalysisBindings:
    clean_surface_analysis_id: AnalysisId
    ooh_adsorbed_analysis_id: AnalysisId
    o_adsorbed_analysis_id: AnalysisId
    oh_adsorbed_analysis_id: AnalysisId
    o2_reference_analysis_id: AnalysisId
    h2o_reference_analysis_id: AnalysisId
    h2_reference_analysis_id: AnalysisId


@dataclass(frozen=True, slots=True)
class OERPresetAnalysisBindings:
    clean_surface_analysis_id: AnalysisId
    oh_adsorbed_analysis_id: AnalysisId
    o_adsorbed_analysis_id: AnalysisId
    ooh_adsorbed_analysis_id: AnalysisId
    h2o_reference_analysis_id: AnalysisId
    o2_reference_analysis_id: AnalysisId
    h2_reference_analysis_id: AnalysisId


@dataclass(frozen=True, slots=True)
class CO2RRToCOPresetAnalysisBindings:
    clean_surface_analysis_id: AnalysisId
    cooh_adsorbed_analysis_id: AnalysisId
    co_adsorbed_analysis_id: AnalysisId
    co2_reference_analysis_id: AnalysisId
    h2o_reference_analysis_id: AnalysisId
    co_reference_analysis_id: AnalysisId
    h2_reference_analysis_id: AnalysisId


ReactionPresetAnalysisBindings = (
    HERPresetAnalysisBindings
    | ORRPresetAnalysisBindings
    | OERPresetAnalysisBindings
    | CO2RRToCOPresetAnalysisBindings
)


@dataclass(frozen=True, slots=True)
class PreparedReactionWorkspace:
    preset: ElectrocatalysisPathwayPreset
    sources: tuple[ReactionEnergySource, ...]
    bindings: tuple[ReactionSourceArtifactBinding, ...]
    baseline: ReactionPathwayResult
    baseline_conditions: CHEConditions
    requested_conditions: CHEConditions
    descriptors: tuple[ReactionDiagramDescriptorDefinition, ...]


class ProjectReactionWorkspaceApplicationService(ProjectApplicationService):
    """Project-scoped reaction preview/materialization over canonical v0.8 scientific data."""

    def preview_preset(
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
        view = evaluate_potential_dependent_pathway_view(
            baseline_pathway=prepared.baseline,
            baseline_conditions=prepared.baseline_conditions,
            target_conditions=prepared.requested_conditions,
        )
        return {
            "preset_kind": preset_kind.value,
            "preset_hash": prepared.preset.preset_hash,
            "pathway_definition": _json_value(prepared.preset.definition),
            "baseline_result": _json_value(prepared.baseline),
            "potential_view": _json_value(view),
            "descriptor_definitions": _json_value(prepared.descriptors),
        }

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
        existing = self._find_existing_diagram(prepared)
        if existing is not None:
            analysis, artifact = existing
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
        current = self.store.open()
        for binding in prepared.bindings:
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
            analysis=durable.analysis,
            artifacts=(durable.artifact,),
            provenance_records=durable.provenance_records,
            dependency_records=durable.dependency_records,
        )
        return {
            "analysis_id": str(durable.analysis.id),
            "artifact_id": str(durable.artifact.id),
            "preset_kind": preset_kind.value,
            "dataset_hash": durable.dataset.result_hash,
            "reused": False,
        }

    def _prepare(
        self,
        *,
        preset_kind: ElectrocatalysisPresetKind,
        bindings: ReactionPresetAnalysisBindings,
        baseline_conditions: CHEConditions,
        requested_conditions: CHEConditions,
    ) -> PreparedReactionWorkspace:
        if baseline_conditions.temperature_k != requested_conditions.temperature_k:
            raise ApplicationServiceError(
                "reaction preview cannot change thermochemistry temperature between conditions"
            )
        if preset_kind is ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY:
            if not isinstance(bindings, HERPresetAnalysisBindings):
                raise ApplicationServiceError("HER preset requires HER analysis bindings")
            return self._prepare_her(bindings, baseline_conditions, requested_conditions)
        if preset_kind is ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E:
            if not isinstance(bindings, ORRPresetAnalysisBindings):
                raise ApplicationServiceError("ORR preset requires ORR analysis bindings")
            return self._prepare_orr(bindings, baseline_conditions, requested_conditions)
        if preset_kind is ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E:
            if not isinstance(bindings, OERPresetAnalysisBindings):
                raise ApplicationServiceError("OER preset requires OER analysis bindings")
            return self._prepare_oer(bindings, baseline_conditions, requested_conditions)
        if preset_kind is ElectrocatalysisPresetKind.CO2RR_TO_CO_2E:
            if not isinstance(bindings, CO2RRToCOPresetAnalysisBindings):
                raise ApplicationServiceError("CO2RR-to-CO preset requires CO2RR analysis bindings")
            return self._prepare_co2rr(bindings, baseline_conditions, requested_conditions)
        raise ApplicationServiceError("unsupported electrocatalysis preset kind")

    def _prepare_her(
        self,
        bindings: HERPresetAnalysisBindings,
        baseline_conditions: CHEConditions,
        requested_conditions: CHEConditions,
    ) -> PreparedReactionWorkspace:
        clean = self._surface(bindings.clean_surface_analysis_id, _CLEAN, ThermochemistrySubjectKind.SURFACE)
        h_star = self._surface(bindings.h_adsorbed_analysis_id, _H_STAR, ThermochemistrySubjectKind.ADSORBATE)
        h2 = self._molecular(bindings.h2_reference_analysis_id, _H2, GasReferenceSpecies.H2)
        che = _che_source(h2, baseline_conditions)
        preset = compile_her_volmer_heyrovsky_preset(
            pathway_key=ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY.value,
            clean_surface_key=_CLEAN,
            h_adsorbed_key=_H_STAR,
            h2_reference_key=_H2,
            che_key=_CHE,
        )
        sources: tuple[ReactionEnergySource, ...] = (
            clean.source,
            h_star.source,
            h2.source,
            che,
        )
        baseline = evaluate_reaction_pathway(definition=preset.definition, sources=sources)
        limiting = solve_limiting_potential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
        reversible = derive_reversible_potential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
        adsorption = evaluate_her_delta_g_h_star(
            step_key="her_h_adsorption",
            label="H* adsorption",
            initial_state_key=_CLEAN,
            final_state_key=_H_STAR,
            hydrogen_adsorbed_source=h_star.source,
            clean_surface_source=clean.source,
            h2_reference=h2.source,
        )
        descriptors = (
            define_limiting_potential_descriptor(key="limiting_potential", result=limiting),
            define_reversible_potential_descriptor(key="reversible_potential", result=reversible),
            define_her_delta_g_h_star_descriptor(key="delta_g_h_star", result=adsorption),
        )
        return _prepared(
            preset=preset,
            sources=sources,
            bindings=(clean.binding, h_star.binding, h2.binding),
            baseline=baseline,
            baseline_conditions=baseline_conditions,
            requested_conditions=requested_conditions,
            descriptors=descriptors,
        )

    def _prepare_orr(
        self,
        bindings: ORRPresetAnalysisBindings,
        baseline_conditions: CHEConditions,
        requested_conditions: CHEConditions,
    ) -> PreparedReactionWorkspace:
        clean = self._surface(bindings.clean_surface_analysis_id, _CLEAN, ThermochemistrySubjectKind.SURFACE)
        ooh = self._surface(bindings.ooh_adsorbed_analysis_id, _OOH_STAR, ThermochemistrySubjectKind.ADSORBATE)
        oxygen = self._surface(bindings.o_adsorbed_analysis_id, _O_STAR, ThermochemistrySubjectKind.ADSORBATE)
        hydroxyl = self._surface(bindings.oh_adsorbed_analysis_id, _OH_STAR, ThermochemistrySubjectKind.ADSORBATE)
        o2 = self._molecular(bindings.o2_reference_analysis_id, _O2, GasReferenceSpecies.O2)
        h2o = self._molecular(bindings.h2o_reference_analysis_id, _H2O, GasReferenceSpecies.H2O)
        h2 = self._molecular(bindings.h2_reference_analysis_id, "CHE_H2", GasReferenceSpecies.H2)
        che = _che_source(h2, baseline_conditions)
        preset = compile_orr_associative_4e_preset(
            pathway_key=ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E.value,
            clean_surface_key=_CLEAN,
            ooh_adsorbed_key=_OOH_STAR,
            o_adsorbed_key=_O_STAR,
            oh_adsorbed_key=_OH_STAR,
            o2_reference_key=_O2,
            h2o_reference_key=_H2O,
            che_key=_CHE,
        )
        sources: tuple[ReactionEnergySource, ...] = (
            clean.source,
            ooh.source,
            oxygen.source,
            hydroxyl.source,
            o2.source,
            h2o.source,
            che,
        )
        return self._prepare_pathway(
            preset=preset,
            sources=sources,
            bindings=(clean.binding, ooh.binding, oxygen.binding, hydroxyl.binding, o2.binding, h2o.binding),
            baseline_conditions=baseline_conditions,
            requested_conditions=requested_conditions,
        )

    def _prepare_oer(
        self,
        bindings: OERPresetAnalysisBindings,
        baseline_conditions: CHEConditions,
        requested_conditions: CHEConditions,
    ) -> PreparedReactionWorkspace:
        clean = self._surface(bindings.clean_surface_analysis_id, _CLEAN, ThermochemistrySubjectKind.SURFACE)
        hydroxyl = self._surface(bindings.oh_adsorbed_analysis_id, _OH_STAR, ThermochemistrySubjectKind.ADSORBATE)
        oxygen = self._surface(bindings.o_adsorbed_analysis_id, _O_STAR, ThermochemistrySubjectKind.ADSORBATE)
        ooh = self._surface(bindings.ooh_adsorbed_analysis_id, _OOH_STAR, ThermochemistrySubjectKind.ADSORBATE)
        h2o = self._molecular(bindings.h2o_reference_analysis_id, _H2O, GasReferenceSpecies.H2O)
        o2 = self._molecular(bindings.o2_reference_analysis_id, _O2, GasReferenceSpecies.O2)
        h2 = self._molecular(bindings.h2_reference_analysis_id, "CHE_H2", GasReferenceSpecies.H2)
        che = _che_source(h2, baseline_conditions)
        preset = compile_oer_associative_4e_preset(
            pathway_key=ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E.value,
            clean_surface_key=_CLEAN,
            oh_adsorbed_key=_OH_STAR,
            o_adsorbed_key=_O_STAR,
            ooh_adsorbed_key=_OOH_STAR,
            h2o_reference_key=_H2O,
            o2_reference_key=_O2,
            che_key=_CHE,
        )
        sources: tuple[ReactionEnergySource, ...] = (
            clean.source,
            hydroxyl.source,
            oxygen.source,
            ooh.source,
            h2o.source,
            o2.source,
            che,
        )
        baseline = evaluate_reaction_pathway(definition=preset.definition, sources=sources)
        limiting = solve_limiting_potential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
        reversible = derive_reversible_potential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
        overpotential = evaluate_oer_theoretical_overpotential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
        descriptors = (
            define_limiting_potential_descriptor(key="limiting_potential", result=limiting),
            define_reversible_potential_descriptor(key="reversible_potential", result=reversible),
            define_oer_overpotential_descriptor(key="oer_overpotential", result=overpotential),
        )
        return _prepared(
            preset=preset,
            sources=sources,
            bindings=(clean.binding, hydroxyl.binding, oxygen.binding, ooh.binding, h2o.binding, o2.binding),
            baseline=baseline,
            baseline_conditions=baseline_conditions,
            requested_conditions=requested_conditions,
            descriptors=descriptors,
        )

    def _prepare_co2rr(
        self,
        bindings: CO2RRToCOPresetAnalysisBindings,
        baseline_conditions: CHEConditions,
        requested_conditions: CHEConditions,
    ) -> PreparedReactionWorkspace:
        clean = self._surface(bindings.clean_surface_analysis_id, _CLEAN, ThermochemistrySubjectKind.SURFACE)
        cooh = self._surface(bindings.cooh_adsorbed_analysis_id, _COOH_STAR, ThermochemistrySubjectKind.ADSORBATE)
        co_star = self._surface(bindings.co_adsorbed_analysis_id, _CO_STAR, ThermochemistrySubjectKind.ADSORBATE)
        co2 = self._molecular(bindings.co2_reference_analysis_id, _CO2, GasReferenceSpecies.CO2)
        h2o = self._molecular(bindings.h2o_reference_analysis_id, _H2O, GasReferenceSpecies.H2O)
        co = self._molecular(bindings.co_reference_analysis_id, _CO, GasReferenceSpecies.CO)
        h2 = self._molecular(bindings.h2_reference_analysis_id, "CHE_H2", GasReferenceSpecies.H2)
        che = _che_source(h2, baseline_conditions)
        preset = compile_co2rr_to_co_2e_preset(
            pathway_key=ElectrocatalysisPresetKind.CO2RR_TO_CO_2E.value,
            clean_surface_key=_CLEAN,
            cooh_adsorbed_key=_COOH_STAR,
            co_adsorbed_key=_CO_STAR,
            co2_reference_key=_CO2,
            h2o_reference_key=_H2O,
            co_reference_key=_CO,
            che_key=_CHE,
        )
        sources: tuple[ReactionEnergySource, ...] = (
            clean.source,
            cooh.source,
            co_star.source,
            co2.source,
            h2o.source,
            co.source,
            che,
        )
        return self._prepare_pathway(
            preset=preset,
            sources=sources,
            bindings=(clean.binding, cooh.binding, co_star.binding, co2.binding, h2o.binding, co.binding),
            baseline_conditions=baseline_conditions,
            requested_conditions=requested_conditions,
        )

    def _prepare_pathway(
        self,
        *,
        preset: ElectrocatalysisPathwayPreset,
        sources: tuple[ReactionEnergySource, ...],
        bindings: tuple[ReactionSourceArtifactBinding, ...],
        baseline_conditions: CHEConditions,
        requested_conditions: CHEConditions,
    ) -> PreparedReactionWorkspace:
        baseline = evaluate_reaction_pathway(definition=preset.definition, sources=sources)
        limiting = solve_limiting_potential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
        reversible = derive_reversible_potential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
        descriptors = (
            define_limiting_potential_descriptor(key="limiting_potential", result=limiting),
            define_reversible_potential_descriptor(key="reversible_potential", result=reversible),
        )
        return _prepared(
            preset=preset,
            sources=sources,
            bindings=bindings,
            baseline=baseline,
            baseline_conditions=baseline_conditions,
            requested_conditions=requested_conditions,
            descriptors=descriptors,
        )

    def _surface(
        self,
        analysis_id: AnalysisId,
        species_key: str,
        expected_subject: ThermochemistrySubjectKind,
    ) -> ResolvedSurfaceReactionSource:
        return resolve_surface_reaction_source(
            store=self.store,
            analysis_id=analysis_id,
            species_key=species_key,
            expected_subject=expected_subject,
        )

    def _molecular(
        self,
        analysis_id: AnalysisId,
        species_key: str,
        expected_species: GasReferenceSpecies,
    ) -> ResolvedMolecularReactionSource:
        return resolve_molecular_reaction_source(
            store=self.store,
            analysis_id=analysis_id,
            species_key=species_key,
            expected_species=expected_species,
        )

    def _find_existing_diagram(
        self,
        prepared: PreparedReactionWorkspace,
    ) -> tuple[Analysis, object] | None:
        bundle = self.store.open()
        expected_bindings = sorted(
            (
                binding.species_key,
                str(binding.analysis.id),
                str(binding.artifact.id),
                binding.artifact.sha256,
            )
            for binding in prepared.bindings
        )
        expected_descriptors = _json_value(prepared.descriptors)
        expected_baseline = _json_value(prepared.baseline_conditions)
        expected_requested = _json_value(prepared.requested_conditions)
        matches: list[tuple[Analysis, object]] = []
        for analysis in bundle.analyses:
            if analysis.analysis_type is not AnalysisType.REACTION_DIAGRAM:
                continue
            if analysis.status is not AnalysisStatus.COMPLETED:
                continue
            if (analysis.tool, analysis.tool_version) != (
                REACTION_DIAGRAM_TOOL_NAME,
                REACTION_DIAGRAM_TOOL_VERSION,
            ):
                continue
            canonical = canonical_analysis_payload(
                root=self.store.root,
                bundle=bundle,
                analysis=analysis,
            )
            dataset = canonical.payload.get("dataset")
            if not isinstance(dataset, dict):
                raise ApplicationServiceError("canonical reaction diagram dataset must be a mapping")
            if dataset.get("pathway_definition_hash") != prepared.preset.definition.content_hash:
                continue
            if dataset.get("baseline_conditions") != expected_baseline:
                continue
            if dataset.get("requested_conditions") != expected_requested:
                continue
            if dataset.get("descriptor_definitions") != expected_descriptors:
                continue
            receipts = dataset.get("source_receipts")
            if not isinstance(receipts, list):
                raise ApplicationServiceError("canonical reaction source receipts must be a list")
            observed_bindings = sorted(
                (
                    str(item.get("species_key")),
                    str(item.get("analysis_id")),
                    str(item.get("artifact_id")),
                    item.get("artifact_sha256"),
                )
                for item in receipts
                if isinstance(item, dict)
            )
            if observed_bindings != expected_bindings:
                continue
            matches.append((analysis, canonical.artifact))
        if len(matches) > 1:
            raise ApplicationServiceError("duplicate exact reaction-diagram Analyses exist")
        return matches[0] if matches else None


def _che_source(
    h2: ResolvedMolecularReactionSource,
    conditions: CHEConditions,
) -> CHEReactionSource:
    hydrogen = CHEHydrogenReference(raw=h2.raw, corrected=h2.corrected)
    chemical_potential = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=conditions,
    )
    return CHEReactionSource(species_key=_CHE, result=chemical_potential)


def _prepared(
    *,
    preset: ElectrocatalysisPathwayPreset,
    sources: tuple[ReactionEnergySource, ...],
    bindings: tuple[ReactionSourceArtifactBinding, ...],
    baseline: ReactionPathwayResult,
    baseline_conditions: CHEConditions,
    requested_conditions: CHEConditions,
    descriptors: tuple[ReactionDiagramDescriptorDefinition, ...],
) -> PreparedReactionWorkspace:
    return PreparedReactionWorkspace(
        preset=preset,
        sources=sources,
        bindings=bindings,
        baseline=baseline,
        baseline_conditions=baseline_conditions,
        requested_conditions=requested_conditions,
        descriptors=descriptors,
    )


def _json_value(value: object) -> object:
    return json.loads(canonical_json(value))


__all__ = [
    "CO2RRToCOPresetAnalysisBindings",
    "HERPresetAnalysisBindings",
    "OERPresetAnalysisBindings",
    "ORRPresetAnalysisBindings",
    "PreparedReactionWorkspace",
    "ProjectReactionWorkspaceApplicationService",
    "ReactionPresetAnalysisBindings",
]
