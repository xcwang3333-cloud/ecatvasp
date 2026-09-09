"""Desktop actions for fixed electrocatalysis reaction presets in v1.1 Block 7."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.reaction_workspace import (
    CO2RRToCOPresetAnalysisBindings,
    HERPresetAnalysisBindings,
    OERPresetAnalysisBindings,
    ORRPresetAnalysisBindings,
    ProjectReactionWorkspaceApplicationService,
    ReactionPresetAnalysisBindings,
)
from ecatvasp.api.thermochemistry_workspace import (
    ProjectThermochemistryApplicationService,
)
from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_reaction import (
    DesktopV2CO2RRToCOAnalysisBindings,
    DesktopV2HERAnalysisBindings,
    DesktopV2OERAnalysisBindings,
    DesktopV2ORRAnalysisBindings,
    DesktopV2ReactionAnalysisBindings,
    DesktopV2ReactionConditions,
)
from ecatvasp.domain import AnalysisId
from ecatvasp.storage import ProjectStore
from ecatvasp.thermo import (
    CHEConditions,
    CHEPhSemantics,
    ElectrocatalysisPresetKind,
    ElectrodePotentialReference,
)


def reaction_preview_action(
    *,
    project_root: Path | str,
    preset_kind: str,
    bindings: DesktopV2ReactionAnalysisBindings,
    baseline_conditions: DesktopV2ReactionConditions,
    requested_conditions: DesktopV2ReactionConditions,
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectReactionWorkspaceApplicationService(ProjectStore(root))
    return {
        "project_root": str(root),
        **service.preview_preset(
            preset_kind=ElectrocatalysisPresetKind(preset_kind),
            bindings=_analysis_bindings(bindings),
            baseline_conditions=_conditions(baseline_conditions),
            requested_conditions=_conditions(requested_conditions),
        ),
    }


def materialize_reaction_diagram_action(
    *,
    project_root: Path | str,
    preset_kind: str,
    bindings: DesktopV2ReactionAnalysisBindings,
    baseline_conditions: DesktopV2ReactionConditions,
    requested_conditions: DesktopV2ReactionConditions,
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectReactionWorkspaceApplicationService(ProjectStore(root))
    return {
        "project_root": str(root),
        **service.materialize_preset_diagram(
            preset_kind=ElectrocatalysisPresetKind(preset_kind),
            bindings=_analysis_bindings(bindings),
            baseline_conditions=_conditions(baseline_conditions),
            requested_conditions=_conditions(requested_conditions),
        ),
    }


def reaction_diagram_view_action(
    *,
    project_root: Path | str,
    analysis_id: str,
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectThermochemistryApplicationService(ProjectStore(root))
    payload = service.analysis_view(analysis_id=_analysis_id(analysis_id))
    if payload.get("analysis_type") != "reaction_diagram":
        raise ApplicationServiceError("Analysis is not a REACTION_DIAGRAM")
    return {"project_root": str(root), **payload}


def _conditions(value: DesktopV2ReactionConditions) -> CHEConditions:
    return CHEConditions(
        temperature_k=value.temperature_k,
        potential_v=value.potential_v,
        ph=value.ph,
        potential_reference=ElectrodePotentialReference(value.potential_reference),
        ph_semantics=CHEPhSemantics(value.ph_semantics),
    )


def _analysis_id(value: str) -> AnalysisId:
    return AnalysisId(UUID(value))


def _analysis_bindings(
    value: DesktopV2ReactionAnalysisBindings,
) -> ReactionPresetAnalysisBindings:
    if isinstance(value, DesktopV2HERAnalysisBindings):
        return HERPresetAnalysisBindings(
            clean_surface_analysis_id=_analysis_id(value.clean_surface_analysis_id),
            h_adsorbed_analysis_id=_analysis_id(value.h_adsorbed_analysis_id),
            h2_reference_analysis_id=_analysis_id(value.h2_reference_analysis_id),
        )
    if isinstance(value, DesktopV2ORRAnalysisBindings):
        return ORRPresetAnalysisBindings(
            clean_surface_analysis_id=_analysis_id(value.clean_surface_analysis_id),
            ooh_adsorbed_analysis_id=_analysis_id(value.ooh_adsorbed_analysis_id),
            o_adsorbed_analysis_id=_analysis_id(value.o_adsorbed_analysis_id),
            oh_adsorbed_analysis_id=_analysis_id(value.oh_adsorbed_analysis_id),
            o2_reference_analysis_id=_analysis_id(value.o2_reference_analysis_id),
            h2o_reference_analysis_id=_analysis_id(value.h2o_reference_analysis_id),
            h2_reference_analysis_id=_analysis_id(value.h2_reference_analysis_id),
        )
    if isinstance(value, DesktopV2OERAnalysisBindings):
        return OERPresetAnalysisBindings(
            clean_surface_analysis_id=_analysis_id(value.clean_surface_analysis_id),
            oh_adsorbed_analysis_id=_analysis_id(value.oh_adsorbed_analysis_id),
            o_adsorbed_analysis_id=_analysis_id(value.o_adsorbed_analysis_id),
            ooh_adsorbed_analysis_id=_analysis_id(value.ooh_adsorbed_analysis_id),
            h2o_reference_analysis_id=_analysis_id(value.h2o_reference_analysis_id),
            o2_reference_analysis_id=_analysis_id(value.o2_reference_analysis_id),
            h2_reference_analysis_id=_analysis_id(value.h2_reference_analysis_id),
        )
    if isinstance(value, DesktopV2CO2RRToCOAnalysisBindings):
        return CO2RRToCOPresetAnalysisBindings(
            clean_surface_analysis_id=_analysis_id(value.clean_surface_analysis_id),
            cooh_adsorbed_analysis_id=_analysis_id(value.cooh_adsorbed_analysis_id),
            co_adsorbed_analysis_id=_analysis_id(value.co_adsorbed_analysis_id),
            co2_reference_analysis_id=_analysis_id(value.co2_reference_analysis_id),
            h2o_reference_analysis_id=_analysis_id(value.h2o_reference_analysis_id),
            co_reference_analysis_id=_analysis_id(value.co_reference_analysis_id),
            h2_reference_analysis_id=_analysis_id(value.h2_reference_analysis_id),
        )
    raise DesktopIPCError("unsupported reaction analysis bindings")


__all__ = [
    "materialize_reaction_diagram_action",
    "reaction_diagram_view_action",
    "reaction_preview_action",
]
