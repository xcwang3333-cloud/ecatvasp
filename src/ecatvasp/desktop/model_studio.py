"""Task-oriented desktop Model Studio actions over the application-service seam."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ecatvasp.api.model_studio import (
    ProjectModelStudioApplicationService,
    create_project_store,
)
from ecatvasp.domain import (
    ActiveSiteId,
    AtomUid,
    BindingMode,
    CatalystId,
    SideLabel,
    SiteSide,
    StructureSnapshotId,
    StructureVariantId,
)
from ecatvasp.storage import ProjectStore
from ecatvasp.structures import (
    AdsorbateContactSpec,
    AdsorbatePlacementSpec,
    DopantSubstitution,
    GrapheneBuildSpec,
    MultiMetalCenterSpec,
    MultiMetalSiteSpec,
    SingleMetalSiteSpec,
    list_adsorbate_templates,
)
from ecatvasp.visualization import build_structure_presentation


@dataclass(frozen=True, slots=True)
class DesktopDopantInput:
    """One explicit atom-identity substitution requested by the desktop."""

    atom_uid: str
    dopant: str


@dataclass(frozen=True, slots=True)
class DesktopMetalCenterInput:
    """One explicit metal center plus exact coordination atom identities."""

    metal_element: str
    coordination_atom_uids: tuple[str, ...]
    side: str
    height_angstrom: float


@dataclass(frozen=True, slots=True)
class DesktopAdsorbateContactInput:
    """One template atom -> active-site atom contact intent."""

    adsorbate_atom_key: str
    site_atom_uid: str


def model_catalog_action(project_root: Path | str) -> dict[str, object]:
    """Build a human-facing Model Studio catalog without full-project presentations."""

    root = Path(project_root)
    bundle = ProjectStore(root).open()
    snapshots = {item.id: item for item in bundle.structure_snapshots}
    catalysts = [
        {
            "catalyst_id": str(item.id),
            "name": item.name,
            "slug": item.slug,
            "formula_label": item.formula_label,
            "support_type": item.support_type,
            "series_key": item.series_key,
            "series_value": item.series_value,
            "tags": list(item.tags),
        }
        for item in bundle.catalysts
    ]
    variants: list[dict[str, object]] = []
    for item in bundle.structure_variants:
        snapshot = (
            snapshots.get(item.current_structure_snapshot_id)
            if item.current_structure_snapshot_id is not None
            else None
        )
        variants.append(
            {
                "structure_variant_id": str(item.id),
                "catalyst_id": str(item.catalyst_id),
                "name": item.name,
                "variant_type": item.variant_type.value,
                "parent_variant_id": (
                    str(item.parent_variant_id) if item.parent_variant_id is not None else None
                ),
                "topology_tags": list(item.topology_tags),
                "current_structure_snapshot_id": (
                    str(item.current_structure_snapshot_id)
                    if item.current_structure_snapshot_id is not None
                    else None
                ),
                "atom_count": len(snapshot.sites) if snapshot is not None else None,
                "structure_label": snapshot.label if snapshot is not None else None,
                "structure_origin": snapshot.origin.value if snapshot is not None else None,
            }
        )
    active_sites = [
        {
            "active_site_id": str(item.id),
            "structure_variant_id": str(item.structure_variant_id),
            "center_atom_uids": [str(value) for value in item.center_atom_uids],
            "topology": item.topology,
            "coordination_environment": item.coordination_environment,
            "side_labels": [
                {"atom_uid": str(label.atom_uid), "side": label.side.value}
                for label in item.side_labels
            ],
        }
        for item in bundle.active_sites
    ]
    states = [
        {
            "adsorption_state_id": str(item.id),
            "structure_variant_id": str(item.structure_variant_id),
            "active_site_id": str(item.active_site_id),
            "state_label": item.state_label,
            "adsorbates": list(item.adsorbates),
            "coverage": item.coverage,
            "reaction_role": item.reaction_role,
        }
        for item in bundle.adsorption_states
    ]
    conformers = [
        {
            "state_conformer_id": str(item.id),
            "adsorption_state_id": str(item.adsorption_state_id),
            "structure_snapshot_id": str(item.structure_snapshot_id),
            "name": item.name,
            "binding_mode": item.binding_mode.value,
            "orientation": item.orientation,
            "rank": item.rank,
        }
        for item in bundle.state_conformers
    ]
    templates = [
        {
            "key": item.key,
            "atom_keys": list(item.atom_keys),
            "anchor_atom_keys": list(item.anchor_atom_keys),
            "primary_anchor_atom_key": item.primary_anchor_atom_key,
            "reaction_families": list(item.reaction_families),
        }
        for item in list_adsorbate_templates()
    ]
    return {
        "project_root": str(root),
        "project_id": str(bundle.project.id),
        "project_name": bundle.project.name,
        "catalysts": catalysts,
        "variants": variants,
        "active_sites": active_sites,
        "adsorption_states": states,
        "state_conformers": conformers,
        "adsorbate_templates": templates,
    }


def structure_presentation_action(
    project_root: Path | str,
    *,
    structure_snapshot_id: str,
) -> dict[str, object]:
    """Build one on-demand identity-preserving structure presentation."""

    root = Path(project_root)
    snapshot_id = StructureSnapshotId(UUID(structure_snapshot_id))
    bundle = ProjectStore(root).open()
    snapshot = next(
        (item for item in bundle.structure_snapshots if item.id == snapshot_id),
        None,
    )
    if snapshot is None:
        raise ValueError("StructureSnapshot is absent from the current ProjectStore")
    presentation = build_structure_presentation(snapshot)
    return {
        "project_root": str(root),
        "presentation": presentation.to_dict(),
    }


def create_project_action(
    *,
    project_root: str,
    name: str,
    slug: str,
    description: str | None,
) -> dict[str, object]:
    receipt = create_project_store(
        project_root,
        name=name,
        slug=slug,
        description=description,
    )
    return {
        "project_root": str(receipt.project_root),
        "project_id": str(receipt.project.id),
        "project_name": receipt.project.name,
        "project_slug": receipt.project.slug,
        "schema_version": receipt.project.schema_version,
    }


def create_catalyst_action(
    *,
    project_root: str,
    name: str,
    slug: str,
    formula_label: str | None,
    support_type: str | None,
    series_key: str | None,
    series_value: str | int | float | None,
    tags: tuple[str, ...],
) -> dict[str, object]:
    catalyst = ProjectModelStudioApplicationService(ProjectStore(project_root)).create_catalyst(
        name=name,
        slug=slug,
        formula_label=formula_label,
        support_type=support_type,
        series_key=series_key,
        series_value=series_value,
        tags=tags,
    )
    return {
        "project_root": project_root,
        "catalyst_id": str(catalyst.id),
        "name": catalyst.name,
        "slug": catalyst.slug,
    }


def build_graphene_action(
    *,
    project_root: str,
    catalyst_id: str,
    variant_name: str,
    nx: int,
    ny: int,
    bond_length_angstrom: float,
    vacuum_gap_angstrom: float,
    label: str | None,
) -> dict[str, object]:
    service = ProjectModelStudioApplicationService(ProjectStore(project_root))
    result = service.build_graphene_model(
        catalyst_id=CatalystId(UUID(catalyst_id)),
        variant_name=variant_name,
        spec=GrapheneBuildSpec(
            nx=nx,
            ny=ny,
            bond_length_angstrom=bond_length_angstrom,
            vacuum_gap_angstrom=vacuum_gap_angstrom,
            label=label,
        ),
    )
    return _structure_model_payload(project_root, result.variant, result.snapshot)


def import_structure_action(
    *,
    project_root: str,
    catalyst_id: str,
    variant_name: str,
    source_path: str,
    format: str | None,
) -> dict[str, object]:
    service = ProjectModelStudioApplicationService(ProjectStore(project_root))
    result, document = service.import_structure_model(
        catalyst_id=CatalystId(UUID(catalyst_id)),
        variant_name=variant_name,
        source_path=source_path,
        format=format,
    )
    return {
        **_structure_model_payload(project_root, result.variant, result.snapshot),
        "source_format": document.format.value,
        "source_name": document.source_name,
    }


def mutate_structure_action(
    *,
    project_root: str,
    source_variant_id: str,
    source_snapshot_id: str,
    variant_name: str,
    vacancy_atom_uids: tuple[str, ...],
    substitutions: tuple[DesktopDopantInput, ...],
    label: str | None,
) -> dict[str, object]:
    service = ProjectModelStudioApplicationService(ProjectStore(project_root))
    result, mutation = service.mutate_structure_model(
        source_variant_id=StructureVariantId(UUID(source_variant_id)),
        source_snapshot_id=StructureSnapshotId(UUID(source_snapshot_id)),
        variant_name=variant_name,
        vacancy_atom_uids=tuple(AtomUid(UUID(value)) for value in vacancy_atom_uids),
        substitutions=tuple(
            DopantSubstitution(AtomUid(UUID(item.atom_uid)), item.dopant)
            for item in substitutions
        ),
        label=label,
    )
    return {
        **_structure_model_payload(project_root, result.variant, result.snapshot),
        "lineage": [
            {
                "action": item.action.value,
                "source_atom_uid": str(item.source_atom_uid),
                "target_atom_uid": (
                    str(item.target_atom_uid) if item.target_atom_uid is not None else None
                ),
                "target_element": item.target_element,
            }
            for item in mutation.lineage
        ],
    }


def build_single_metal_action(
    *,
    project_root: str,
    source_variant_id: str,
    source_snapshot_id: str,
    variant_name: str,
    metal_element: str,
    coordination_atom_uids: tuple[str, ...],
    side: str,
    height_angstrom: float,
    label: str | None,
) -> dict[str, object]:
    service = ProjectModelStudioApplicationService(ProjectStore(project_root))
    result = service.build_single_metal_model(
        source_variant_id=StructureVariantId(UUID(source_variant_id)),
        source_snapshot_id=StructureSnapshotId(UUID(source_snapshot_id)),
        variant_name=variant_name,
        spec=SingleMetalSiteSpec(
            metal_element=metal_element,
            coordination_atom_uids=tuple(AtomUid(UUID(value)) for value in coordination_atom_uids),
            side=SiteSide(side),
            height_angstrom=height_angstrom,
            label=label,
        ),
    )
    return {
        **_structure_model_payload(project_root, result.variant, result.build.snapshot),
        "metal_atom_uid": str(result.build.metal_atom_uid),
        "coordination_atom_uids": [str(value) for value in result.build.coordination_atom_uids],
        "coordination_signature": result.build.coordination_signature,
    }


def build_multi_metal_action(
    *,
    project_root: str,
    source_variant_id: str,
    source_snapshot_id: str,
    variant_name: str,
    centers: tuple[DesktopMetalCenterInput, ...],
    metal_metal_topology_intent: str | None,
    label: str | None,
) -> dict[str, object]:
    service = ProjectModelStudioApplicationService(ProjectStore(project_root))
    result = service.build_multi_metal_model(
        source_variant_id=StructureVariantId(UUID(source_variant_id)),
        source_snapshot_id=StructureSnapshotId(UUID(source_snapshot_id)),
        variant_name=variant_name,
        spec=MultiMetalSiteSpec(
            centers=tuple(
                MultiMetalCenterSpec(
                    metal_element=item.metal_element,
                    coordination_atom_uids=tuple(
                        AtomUid(UUID(value)) for value in item.coordination_atom_uids
                    ),
                    side=SiteSide(item.side),
                    height_angstrom=item.height_angstrom,
                )
                for item in centers
            ),
            metal_metal_topology_intent=metal_metal_topology_intent,
            label=label,
        ),
    )
    return {
        **_structure_model_payload(project_root, result.variant, result.build.snapshot),
        "metal_atom_uids": [str(value) for value in result.build.metal_atom_uids],
        "side_topology": result.build.side_topology.value,
        "metal_pair_distances_angstrom": list(result.build.metal_pair_distances_angstrom),
    }


def create_active_site_action(
    *,
    project_root: str,
    structure_variant_id: str,
    source_snapshot_id: str,
    center_atom_uids: tuple[str, ...],
    side_labels: tuple[tuple[str, str], ...],
    topology: str | None,
    coordination_environment: str | None,
) -> dict[str, object]:
    service = ProjectModelStudioApplicationService(ProjectStore(project_root))
    active_site = service.create_active_site(
        structure_variant_id=StructureVariantId(UUID(structure_variant_id)),
        source_snapshot_id=StructureSnapshotId(UUID(source_snapshot_id)),
        center_atom_uids=tuple(AtomUid(UUID(value)) for value in center_atom_uids),
        side_labels=tuple(
            SideLabel(atom_uid=AtomUid(UUID(atom_uid)), side=SiteSide(side))
            for atom_uid, side in side_labels
        ),
        topology=topology,
        coordination_environment=coordination_environment,
    )
    return {
        "project_root": project_root,
        "active_site_id": str(active_site.id),
        "structure_variant_id": str(active_site.structure_variant_id),
        "center_atom_uids": [str(value) for value in active_site.center_atom_uids],
        "topology": active_site.topology,
    }


def build_adsorbate_conformer_action(
    *,
    project_root: str,
    structure_variant_id: str,
    source_snapshot_id: str,
    active_site_id: str,
    state_label: str,
    template_key: str,
    target_center_atom_uids: tuple[str, ...],
    binding_mode: str,
    height_angstrom: float,
    contacts: tuple[DesktopAdsorbateContactInput, ...],
    conformer_name: str,
    coverage: float | None,
    reaction_role: str | None,
    orientation: str | None,
    rank: int | None,
) -> dict[str, object]:
    service = ProjectModelStudioApplicationService(ProjectStore(project_root))
    result = service.build_adsorbate_conformer(
        structure_variant_id=StructureVariantId(UUID(structure_variant_id)),
        source_snapshot_id=StructureSnapshotId(UUID(source_snapshot_id)),
        active_site_id=ActiveSiteId(UUID(active_site_id)),
        state_label=state_label,
        placement=AdsorbatePlacementSpec(
            template_key=template_key,
            target_center_atom_uids=tuple(
                AtomUid(UUID(value)) for value in target_center_atom_uids
            ),
            binding_mode=BindingMode(binding_mode),
            height_angstrom=height_angstrom,
            contacts=tuple(
                AdsorbateContactSpec(
                    item.adsorbate_atom_key,
                    AtomUid(UUID(item.site_atom_uid)),
                )
                for item in contacts
            ),
        ),
        conformer_name=conformer_name,
        coverage=coverage,
        reaction_role=reaction_role,
        orientation=orientation,
        rank=rank,
    )
    return {
        "project_root": project_root,
        "adsorption_state_id": str(result.state.id),
        "state_conformer_id": str(result.conformer.id),
        "structure_snapshot_id": str(result.build.snapshot.id),
        "state_label": result.state.state_label,
        "conformer_name": result.conformer.name,
        "adsorbate_atom_uids": [str(value) for value in result.build.adsorbate_atom_uids],
    }


def _structure_model_payload(
    project_root: str,
    variant: object,
    snapshot: object,
) -> dict[str, object]:
    structure_variant = variant
    structure_snapshot = snapshot
    return {
        "project_root": project_root,
        "structure_variant_id": str(structure_variant.id),
        "variant_name": structure_variant.name,
        "variant_type": structure_variant.variant_type.value,
        "parent_variant_id": (
            str(structure_variant.parent_variant_id)
            if structure_variant.parent_variant_id is not None
            else None
        ),
        "structure_snapshot_id": str(structure_snapshot.id),
        "parent_snapshot_id": (
            str(structure_snapshot.parent_snapshot_id)
            if structure_snapshot.parent_snapshot_id is not None
            else None
        ),
        "atom_count": len(structure_snapshot.sites),
    }
