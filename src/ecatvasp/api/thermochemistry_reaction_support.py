"""Reaction/CHE composition support for the v1.1 Thermochemistry Workspace.

This module contains application plumbing only.  It resolves already-durable v0.8 thermochemistry
Analyses, reconstructs their frozen value objects, compiles a named v0.8 electrocatalysis preset,
and delegates every free-energy/CHE/descriptor/diagram calculation to existing authorities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.thermochemistry_codec import (
    decode_gas_reference,
    decode_reference_result,
    decode_thermochemistry_result,
)
from ecatvasp.api.thermochemistry_workspace_support import (
    CanonicalAnalysisPayload,
    canonical_analysis_payload,
    persist_analysis_graph,
    require_analysis,
    require_artifact,
)
from ecatvasp.domain import Analysis, AnalysisId, Artifact, ArtifactId, canonical_sha256
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.thermo import (
    CHEConditions,
    CHEHydrogenReference,
    CHEPhSemantics,
    CHEReactionSource,
    DurableReactionDiagram,
    ElectrocatalysisPathwayPreset,
    ElectrocatalysisPresetKind,
    ElectrodePotentialReference,
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    MolecularReferenceReactionSource,
    REACTION_DIAGRAM_TOOL_NAME,
    REACTION_DIAGRAM_TOOL_VERSION,
    REFERENCE_CORRECTION_TOOL_NAME,
    ReactionDiagramDescriptorDefinition,
    ReactionEnergySource,
    ReactionPathwayResult,
    ReactionSourceArtifactBinding,
    ReferencePhase,
    ThermochemistryReactionSource,
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

CHE_SPECIES_KEY = "che"

_SURFACE_ROLES = frozenset(
    {
        "clean_surface",
        "h_adsorbed",
        "ooh_adsorbed",
        "o_adsorbed",
        "oh_adsorbed",
        "cooh_adsorbed",
        "co_adsorbed",
    }
)
_GAS_ROLE_SPECIES = {
    "h2_reference": "H2",
    "h2o_reference": "H2O",
    "o2_reference": "O2",
    "co_reference": "CO",
    "co2_reference": "CO2",
}
_PRESET_ROLES: dict[ElectrocatalysisPresetKind, frozenset[str]] = {
    ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY: frozenset(
        {"clean_surface", "h_adsorbed", "h2_reference"}
    ),
    ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E: frozenset(
        {
            "clean_surface",
            "ooh_adsorbed",
            "o_adsorbed",
            "oh_adsorbed",
            "o2_reference",
            "h2o_reference",
            "h2_reference",
        }
    ),
    ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E: frozenset(
        {
            "clean_surface",
            "oh_adsorbed",
            "o_adsorbed",
            "ooh_adsorbed",
            "h2o_reference",
            "o2_reference",
            "h2_reference",
        }
    ),
    ElectrocatalysisPresetKind.CO2RR_TO_CO_2E: frozenset(
        {
            "clean_surface",
            "cooh_adsorbed",
            "co_adsorbed",
            "co2_reference",
            "h2o_reference",
            "co_reference",
            "h2_reference",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ReactionRoleBinding:
    """One fixed preset role bound only to an existing THERMOCHEMISTRY Analysis id."""

    role: str
    analysis_id: AnalysisId


@dataclass(frozen=True, slots=True)
class ResolvedReactionRole:
    role: str
    analysis: Analysis
    artifact: Artifact
    source: ThermochemistryReactionSource | MolecularReferenceReactionSource

    @property
    def binding(self) -> ReactionSourceArtifactBinding:
        return ReactionSourceArtifactBinding(
            species_key=self.role,
            analysis=self.analysis,
            artifact=self.artifact,
        )


@dataclass(frozen=True, slots=True)
class ReactionWorkspaceContext:
    """Resolved preset, scientific sources, baseline result, descriptors, and view."""

    preset: ElectrocatalysisPathwayPreset
    roles: tuple[ResolvedReactionRole, ...]
    pathway_sources: tuple[ReactionEnergySource, ...]
    pathway_bindings: tuple[ReactionSourceArtifactBinding, ...]
    baseline_conditions: CHEConditions
    requested_conditions: CHEConditions
    baseline_result: ReactionPathwayResult
    descriptor_definitions: tuple[ReactionDiagramDescriptorDefinition, ...]
    preview_payload: dict[str, object]


def required_roles(preset_kind: ElectrocatalysisPresetKind) -> tuple[str, ...]:
    try:
        return tuple(sorted(_PRESET_ROLES[preset_kind]))
    except KeyError as error:
        raise ApplicationServiceError("unsupported electrocatalysis preset kind") from error


def build_reaction_workspace_context(
    *,
    store: ProjectStore,
    preset_kind: ElectrocatalysisPresetKind,
    role_bindings: tuple[ReactionRoleBinding, ...],
    baseline_potential_v: float,
    baseline_ph: float,
    baseline_potential_reference: ElectrodePotentialReference,
    baseline_ph_semantics: CHEPhSemantics,
    requested_potential_v: float,
    requested_ph: float,
    requested_potential_reference: ElectrodePotentialReference,
    requested_ph_semantics: CHEPhSemantics,
) -> ReactionWorkspaceContext:
    """Resolve one preset without accepting energies, paths, hashes, or reaction equations."""

    bundle = store.open()
    expected_roles = set(required_roles(preset_kind))
    role_map: dict[str, AnalysisId] = {}
    for binding in role_bindings:
        if binding.role in role_map:
            raise ApplicationServiceError("reaction preset role bindings must be unique")
        role_map[binding.role] = binding.analysis_id
    actual_roles = set(role_map)
    if actual_roles != expected_roles:
        raise ApplicationServiceError(
            "reaction preset roles must match the fixed preset contract; "
            f"missing={sorted(expected_roles - actual_roles)}, "
            f"extra={sorted(actual_roles - expected_roles)}"
        )

    resolved = tuple(
        _resolve_role(
            store=store,
            bundle=bundle,
            role=role,
            analysis_id=role_map[role],
        )
        for role in sorted(role_map)
    )
    resolved_map = {item.role: item for item in resolved}
    h2 = _require_molecular_role(resolved_map, "h2_reference")
    hydrogen_reference = CHEHydrogenReference(
        raw=h2.source.raw,
        corrected=h2.source.corrected,
    )
    temperature_k = hydrogen_reference.temperature_k
    baseline_conditions = CHEConditions(
        temperature_k=temperature_k,
        potential_v=baseline_potential_v,
        ph=baseline_ph,
        potential_reference=baseline_potential_reference,
        ph_semantics=baseline_ph_semantics,
    )
    requested_conditions = CHEConditions(
        temperature_k=temperature_k,
        potential_v=requested_potential_v,
        ph=requested_ph,
        potential_reference=requested_potential_reference,
        ph_semantics=requested_ph_semantics,
    )
    che_result = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen_reference,
        conditions=baseline_conditions,
    )
    che_source = CHEReactionSource(species_key=CHE_SPECIES_KEY, result=che_result)

    preset = _compile_preset(preset_kind)
    definition_keys = {
        term.species_key for step in preset.definition.steps for term in step.terms
    }
    non_che_keys = definition_keys - {CHE_SPECIES_KEY}
    sources: list[ReactionEnergySource] = [
        _require_resolved_role(resolved_map, key).source for key in sorted(non_che_keys)
    ]
    if CHE_SPECIES_KEY in definition_keys:
        sources.append(che_source)
    pathway_sources = tuple(sources)
    pathway_bindings = tuple(
        _require_resolved_role(resolved_map, key).binding for key in sorted(non_che_keys)
    )

    baseline = evaluate_reaction_pathway(
        definition=preset.definition,
        sources=pathway_sources,
    )
    view = evaluate_potential_dependent_pathway_view(
        baseline_pathway=baseline,
        baseline_conditions=baseline_conditions,
        target_conditions=requested_conditions,
    )
    descriptors, descriptor_payload = _descriptors(
        preset_kind=preset_kind,
        resolved_map=resolved_map,
        baseline=baseline,
        baseline_conditions=baseline_conditions,
    )
    preview_payload = {
        "preset_kind": preset.kind.value,
        "reaction_family": preset.family.value,
        "preset_hash": preset.preset_hash,
        "pathway_hash": preset.definition.content_hash,
        "baseline_result_hash": baseline.result_hash,
        "baseline_conditions": _condition_payload(baseline_conditions),
        "requested_conditions": _condition_payload(requested_conditions),
        "state_keys": list(view.state_keys),
        "baseline_step_delta_g_ev": [item.delta_g_ev for item in baseline.step_results],
        "requested_step_delta_g_ev": [item.target_delta_g_ev for item in view.step_views],
        "baseline_cumulative_state_free_energies_ev": list(
            baseline.cumulative_state_free_energies_ev
        ),
        "requested_cumulative_state_free_energies_ev": list(
            view.cumulative_state_free_energies_ev
        ),
        "potential_view_hash": view.result_hash,
        "descriptors": descriptor_payload,
        "source_roles": [
            {
                "role": item.role,
                "analysis_id": str(item.analysis.id),
                "artifact_id": str(item.artifact.id),
                "source_kind": item.source.kind.value,
            }
            for item in resolved
        ],
    }
    return ReactionWorkspaceContext(
        preset=preset,
        roles=resolved,
        pathway_sources=pathway_sources,
        pathway_bindings=pathway_bindings,
        baseline_conditions=baseline_conditions,
        requested_conditions=requested_conditions,
        baseline_result=baseline,
        descriptor_definitions=descriptors,
        preview_payload=preview_payload,
    )


def find_existing_reaction_diagram(
    *,
    store: ProjectStore,
    context: ReactionWorkspaceContext,
) -> tuple[Analysis, Artifact] | None:
    """Find the unique exact durable diagram for one fully resolved deterministic context."""

    bundle = store.open()
    expected_artifact_ids = tuple(
        sorted({item.artifact.id for item in context.pathway_bindings}, key=str)
    )
    expected_descriptor_hashes = tuple(
        sorted(item.definition_hash for item in context.descriptor_definitions)
    )
    matches: list[tuple[Analysis, Artifact]] = []
    for analysis in bundle.analyses:
        if analysis.analysis_type.value != "reaction_diagram":
            continue
        if (analysis.tool, analysis.tool_version) != (
            REACTION_DIAGRAM_TOOL_NAME,
            REACTION_DIAGRAM_TOOL_VERSION,
        ):
            continue
        if analysis.input_artifact_ids != expected_artifact_ids:
            continue
        canonical = canonical_analysis_payload(
            root=store.root,
            bundle=bundle,
            analysis=analysis,
        )
        dataset = _mapping(canonical.payload.get("dataset"), "reaction diagram dataset")
        if dataset.get("pathway_definition_hash") != context.preset.definition.content_hash:
            continue
        if dataset.get("baseline_pathway_result_hash") != context.baseline_result.result_hash:
            continue
        if canonical_sha256(dataset.get("baseline_conditions")) != canonical_sha256(
            context.baseline_conditions
        ):
            continue
        if canonical_sha256(dataset.get("requested_conditions")) != canonical_sha256(
            context.requested_conditions
        ):
            continue
        descriptor_items = _list(dataset.get("descriptor_definitions", []), "descriptors")
        descriptor_hashes = tuple(
            sorted(
                _required_string(_mapping(item, "descriptor"), "definition_hash")
                for item in descriptor_items
            )
        )
        if descriptor_hashes != expected_descriptor_hashes:
            continue
        matches.append((analysis, canonical.artifact))
    if len(matches) > 1:
        raise ApplicationServiceError("duplicate exact reaction-diagram Analyses exist")
    return matches[0] if matches else None


def materialize_context_reaction_diagram(
    *,
    store: ProjectStore,
    context: ReactionWorkspaceContext,
) -> DurableReactionDiagram:
    """Delegate durable materialization to the frozen v0.8 reaction-diagram authority."""

    current = store.open()
    _require_roles_unchanged(current, context.roles)
    materialization = materialize_reaction_diagram(
        project_root=store.root,
        definition=context.preset.definition,
        sources=context.pathway_sources,
        source_bindings=context.pathway_bindings,
        baseline_conditions=context.baseline_conditions,
        requested_conditions=context.requested_conditions,
        descriptor_definitions=context.descriptor_definitions,
    )
    current_after = store.open()
    _require_roles_unchanged(current_after, context.roles)
    persist_analysis_graph(
        store=store,
        bundle=current_after,
        analysis=materialization.analysis,
        artifacts=(materialization.artifact,),
        provenance_records=materialization.provenance_records,
        dependency_records=materialization.dependency_records,
    )
    return materialization


def _resolve_role(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    role: str,
    analysis_id: AnalysisId,
) -> ResolvedReactionRole:
    if role not in _SURFACE_ROLES and role not in _GAS_ROLE_SPECIES:
        raise ApplicationServiceError(f"unsupported reaction preset role: {role}")
    analysis = require_analysis(bundle, analysis_id)
    canonical = canonical_analysis_payload(root=store.root, bundle=bundle, analysis=analysis)
    if role in _SURFACE_ROLES:
        if analysis.tool != HARMONIC_THERMOCHEMISTRY_TOOL_NAME:
            raise ApplicationServiceError(f"reaction role {role} requires harmonic thermochemistry")
        result = decode_thermochemistry_result(canonical.payload.get("result"))
        if result.identity.subject_kind is ThermochemistrySubjectKind.GAS:
            raise ApplicationServiceError(f"reaction role {role} cannot use gas thermochemistry")
        source = ThermochemistryReactionSource(species_key=role, result=result)
        return ResolvedReactionRole(
            role=role,
            analysis=analysis,
            artifact=canonical.artifact,
            source=source,
        )

    source = _molecular_source(
        bundle=bundle,
        store=store,
        role=role,
        analysis=analysis,
        canonical=canonical,
    )
    expected_species = _GAS_ROLE_SPECIES[role]
    if source.raw.reference.species.value != expected_species:
        raise ApplicationServiceError(
            f"reaction role {role} requires molecular reference {expected_species}"
        )
    return ResolvedReactionRole(
        role=role,
        analysis=analysis,
        artifact=canonical.artifact,
        source=source,
    )


def _molecular_source(
    *,
    bundle: ProjectBundle,
    store: ProjectStore,
    role: str,
    analysis: Analysis,
    canonical: CanonicalAnalysisPayload,
) -> MolecularReferenceReactionSource:
    if analysis.tool == IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME:
        raw_result = decode_thermochemistry_result(canonical.payload.get("result"))
        receipt = _mapping(canonical.payload.get("source_receipt"), "gas source receipt")
        reference = decode_gas_reference(receipt.get("reference"))
        from ecatvasp.thermo import BoundGasReferenceThermochemistry

        raw = BoundGasReferenceThermochemistry(reference=reference, result=raw_result)
        return MolecularReferenceReactionSource(species_key=role, raw=raw)

    if analysis.tool != REFERENCE_CORRECTION_TOOL_NAME:
        raise ApplicationServiceError(
            f"reaction role {role} requires ideal-gas or corrected molecular thermochemistry"
        )
    corrected = decode_reference_result(canonical.payload.get("result"))
    receipt = _mapping(canonical.payload.get("source_receipt"), "reference source receipt")
    source_analysis_id = AnalysisId(
        _uuid(_required_string(receipt, "source_analysis_id"), "source_analysis_id")
    )
    source_artifact_id = ArtifactId(
        _uuid(_required_string(receipt, "source_artifact_id"), "source_artifact_id")
    )
    raw_analysis = require_analysis(bundle, source_analysis_id)
    raw_artifact = require_artifact(bundle, source_artifact_id)
    raw_canonical = canonical_analysis_payload(
        root=store.root,
        bundle=bundle,
        analysis=raw_analysis,
    )
    if raw_canonical.artifact != raw_artifact:
        raise ApplicationServiceError("corrected molecular reference points to another raw Artifact")
    if raw_analysis.tool != IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME:
        raise ApplicationServiceError("corrected molecular reference raw source is not ideal-gas")
    raw_result = decode_thermochemistry_result(raw_canonical.payload.get("result"))
    raw_receipt = _mapping(raw_canonical.payload.get("source_receipt"), "raw gas receipt")
    reference = decode_gas_reference(raw_receipt.get("reference"))
    if corrected.adjustment.reference != reference:
        raise ApplicationServiceError("corrected molecular reference species differs from raw source")
    from ecatvasp.thermo import BoundGasReferenceThermochemistry

    raw = BoundGasReferenceThermochemistry(reference=reference, result=raw_result)
    if corrected.adjustment.target_phase is ReferencePhase.LIQUID_WATER and role != "h2o_reference":
        raise ApplicationServiceError("liquid-water correction is valid only for H2O reaction role")
    return MolecularReferenceReactionSource(
        species_key=role,
        raw=raw,
        corrected=corrected,
    )


def _compile_preset(kind: ElectrocatalysisPresetKind) -> ElectrocatalysisPathwayPreset:
    pathway_key = f"ecatvasp-v11-{kind.value}"
    if kind is ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY:
        return compile_her_volmer_heyrovsky_preset(
            pathway_key=pathway_key,
            clean_surface_key="clean_surface",
            h_adsorbed_key="h_adsorbed",
            h2_reference_key="h2_reference",
            che_key=CHE_SPECIES_KEY,
        )
    if kind is ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E:
        return compile_orr_associative_4e_preset(
            pathway_key=pathway_key,
            clean_surface_key="clean_surface",
            ooh_adsorbed_key="ooh_adsorbed",
            o_adsorbed_key="o_adsorbed",
            oh_adsorbed_key="oh_adsorbed",
            o2_reference_key="o2_reference",
            h2o_reference_key="h2o_reference",
            che_key=CHE_SPECIES_KEY,
        )
    if kind is ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E:
        return compile_oer_associative_4e_preset(
            pathway_key=pathway_key,
            clean_surface_key="clean_surface",
            oh_adsorbed_key="oh_adsorbed",
            o_adsorbed_key="o_adsorbed",
            ooh_adsorbed_key="ooh_adsorbed",
            h2o_reference_key="h2o_reference",
            o2_reference_key="o2_reference",
            che_key=CHE_SPECIES_KEY,
        )
    if kind is ElectrocatalysisPresetKind.CO2RR_TO_CO_2E:
        return compile_co2rr_to_co_2e_preset(
            pathway_key=pathway_key,
            clean_surface_key="clean_surface",
            cooh_adsorbed_key="cooh_adsorbed",
            co_adsorbed_key="co_adsorbed",
            co2_reference_key="co2_reference",
            h2o_reference_key="h2o_reference",
            co_reference_key="co_reference",
            che_key=CHE_SPECIES_KEY,
        )
    raise ApplicationServiceError("unsupported electrocatalysis preset kind")


def _descriptors(
    *,
    preset_kind: ElectrocatalysisPresetKind,
    resolved_map: dict[str, ResolvedReactionRole],
    baseline: ReactionPathwayResult,
    baseline_conditions: CHEConditions,
) -> tuple[tuple[ReactionDiagramDescriptorDefinition, ...], list[dict[str, object]]]:
    definitions: list[ReactionDiagramDescriptorDefinition] = []
    payload: list[dict[str, object]] = []

    reversible = derive_reversible_potential(
        baseline_pathway=baseline,
        baseline_conditions=baseline_conditions,
    )
    reversible_definition = define_reversible_potential_descriptor(
        key="reversible_potential",
        result=reversible,
    )
    definitions.append(reversible_definition)
    payload.append(_descriptor_payload(reversible_definition, status="available"))

    try:
        limiting = solve_limiting_potential(
            baseline_pathway=baseline,
            baseline_conditions=baseline_conditions,
        )
    except ValueError as error:
        payload.append(
            {
                "key": "limiting_potential",
                "kind": "limiting_potential",
                "status": "unavailable",
                "reason": str(error),
            }
        )
    else:
        limiting_definition = define_limiting_potential_descriptor(
            key="limiting_potential",
            result=limiting,
        )
        definitions.append(limiting_definition)
        payload.append(_descriptor_payload(limiting_definition, status="available"))

    if preset_kind is ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E:
        try:
            oer = evaluate_oer_theoretical_overpotential(
                baseline_pathway=baseline,
                baseline_conditions=baseline_conditions,
            )
        except ValueError as error:
            payload.append(
                {
                    "key": "oer_theoretical_overpotential",
                    "kind": "oer_theoretical_overpotential",
                    "status": "unavailable",
                    "reason": str(error),
                }
            )
        else:
            oer_definition = define_oer_overpotential_descriptor(
                key="oer_theoretical_overpotential",
                result=oer,
            )
            definitions.append(oer_definition)
            payload.append(_descriptor_payload(oer_definition, status="available"))

    if preset_kind is ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY:
        clean = _require_surface_role(resolved_map, "clean_surface")
        adsorbed = _require_surface_role(resolved_map, "h_adsorbed")
        h2 = _require_molecular_role(resolved_map, "h2_reference")
        her = evaluate_her_delta_g_h_star(
            step_key="her_h_adsorption",
            label="H* adsorption",
            initial_state_key="clean_surface",
            final_state_key="h_adsorbed",
            hydrogen_adsorbed_source=adsorbed.source,
            clean_surface_source=clean.source,
            h2_reference=h2.source,
        )
        her_definition = define_her_delta_g_h_star_descriptor(
            key="her_delta_g_h_star",
            result=her,
        )
        definitions.append(her_definition)
        payload.append(_descriptor_payload(her_definition, status="available"))

    definitions.sort(key=lambda item: item.key)
    payload.sort(key=lambda item: cast(str, item["key"]))
    return tuple(definitions), payload


def _descriptor_payload(
    definition: ReactionDiagramDescriptorDefinition,
    *,
    status: str,
) -> dict[str, object]:
    return {
        "key": definition.key,
        "kind": definition.kind.value,
        "status": status,
        "value": definition.value,
        "unit": definition.unit.value,
        "definition_hash": definition.definition_hash,
        "source_result_hash": definition.source_result_hash,
    }


def _condition_payload(conditions: CHEConditions) -> dict[str, object]:
    return {
        "temperature_k": conditions.temperature_k,
        "potential_v": conditions.potential_v,
        "ph": conditions.ph,
        "potential_reference": conditions.potential_reference.value,
        "ph_semantics": conditions.ph_semantics.value,
        "parameters_hash": conditions.parameters_hash,
    }


def _require_resolved_role(
    mapping: dict[str, ResolvedReactionRole],
    role: str,
) -> ResolvedReactionRole:
    try:
        return mapping[role]
    except KeyError as error:
        raise ApplicationServiceError(f"reaction preset lacks required role {role}") from error


def _require_surface_role(
    mapping: dict[str, ResolvedReactionRole],
    role: str,
) -> ResolvedReactionRole:
    resolved = _require_resolved_role(mapping, role)
    if not isinstance(resolved.source, ThermochemistryReactionSource):
        raise ApplicationServiceError(f"reaction role {role} requires surface thermochemistry")
    return resolved


def _require_molecular_role(
    mapping: dict[str, ResolvedReactionRole],
    role: str,
) -> ResolvedReactionRole:
    resolved = _require_resolved_role(mapping, role)
    if not isinstance(resolved.source, MolecularReferenceReactionSource):
        raise ApplicationServiceError(f"reaction role {role} requires molecular thermochemistry")
    return resolved


def _require_roles_unchanged(
    bundle: ProjectBundle,
    roles: tuple[ResolvedReactionRole, ...],
) -> None:
    for role in roles:
        if require_analysis(bundle, role.analysis.id) != role.analysis:
            raise ApplicationServiceError(
                f"reaction role {role.role} Analysis changed during materialization"
            )
        if require_artifact(bundle, role.artifact.id) != role.artifact:
            raise ApplicationServiceError(
                f"reaction role {role.role} Artifact changed during materialization"
            )


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ApplicationServiceError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ApplicationServiceError(f"{label} must be a JSON array")
    return cast(list[object], value)


def _required_string(raw: dict[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ApplicationServiceError(f"{field} must be a non-blank string")
    return value


def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ApplicationServiceError(f"{field} must be a UUID") from error


__all__ = [
    "ReactionRoleBinding",
    "ReactionWorkspaceContext",
    "build_reaction_workspace_context",
    "find_existing_reaction_diagram",
    "materialize_context_reaction_diagram",
    "required_roles",
]
