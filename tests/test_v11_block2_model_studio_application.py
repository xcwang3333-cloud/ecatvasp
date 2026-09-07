from __future__ import annotations

from pathlib import Path

import pytest

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.model_studio import (
    ProjectModelStudioApplicationService,
    create_project_store,
)
from ecatvasp.domain import BindingMode, SiteSide
from ecatvasp.storage import ProjectStore
from ecatvasp.structures import (
    AdsorbateContactSpec,
    AdsorbatePlacementSpec,
    DopantSubstitution,
    GrapheneBuildSpec,
    MultiMetalCenterSpec,
    MultiMetalSiteSpec,
    SingleMetalSiteSpec,
)


def _service(tmp_path: Path) -> tuple[Path, ProjectModelStudioApplicationService]:
    root = tmp_path / "project"
    create_project_store(root, name="Block 2", slug="block-2")
    return root, ProjectModelStudioApplicationService(ProjectStore(root))


def _graphene(
    service: ProjectModelStudioApplicationService,
    *,
    catalyst_name: str = "FeNC",
):
    catalyst = service.create_catalyst(
        name=catalyst_name,
        slug=catalyst_name.casefold(),
        formula_label="Fe-N-C",
        support_type="graphene",
    )
    model = service.build_graphene_model(
        catalyst_id=catalyst.id,
        variant_name="graphene",
        spec=GrapheneBuildSpec(nx=3, ny=3, vacuum_angstrom=15.0),
    )
    return catalyst, model


def test_create_project_store_is_schema3_and_refuses_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "new-project"
    receipt = create_project_store(root, name="Project", slug="project")

    reopened = ProjectStore(root).open()
    assert receipt.project_root == root
    assert reopened.project == receipt.project
    assert reopened.project.schema_version == 3

    with pytest.raises(ApplicationServiceError, match="new or empty directory"):
        create_project_store(root, name="Other", slug="other")


def test_catalyst_and_graphene_are_persisted_without_frontend_bundle_construction(
    tmp_path: Path,
) -> None:
    root, service = _service(tmp_path)
    catalyst, model = _graphene(service)

    reopened = ProjectStore(root).open()
    assert reopened.catalysts == (catalyst,)
    assert reopened.structure_variants == (model.variant,)
    assert reopened.structure_snapshots == (model.snapshot,)
    assert model.variant.current_structure_snapshot_id == model.snapshot.id
    assert model.variant.parent_variant_id is None
    assert len(model.snapshot.sites) == 18

    with pytest.raises(ApplicationServiceError, match="slug already exists"):
        service.create_catalyst(name="Duplicate", slug="fenc")


def test_mutation_creates_child_variant_and_preserves_uid_lineage(tmp_path: Path) -> None:
    root, service = _service(tmp_path)
    _, model = _graphene(service)
    first, second = model.snapshot.sites[:2]

    child, mutation = service.mutate_structure_model(
        source_variant_id=model.variant.id,
        source_snapshot_id=model.snapshot.id,
        variant_name="N-vacancy",
        vacancy_atom_uids=(first.atom_uid,),
        substitutions=(DopantSubstitution(second.atom_uid, "N"),),
    )

    reopened = ProjectStore(root).open()
    assert child.variant.parent_variant_id == model.variant.id
    assert child.snapshot.parent_snapshot_id == model.snapshot.id
    assert child.variant.current_structure_snapshot_id == child.snapshot.id
    assert child.snapshot.id in {item.id for item in reopened.structure_snapshots}
    assert first.atom_uid not in {site.atom_uid for site in child.snapshot.sites}
    doped = next(site for site in child.snapshot.sites if site.element == "N")
    assert doped.atom_uid != second.atom_uid
    preserved_source = model.snapshot.sites[2].atom_uid
    assert preserved_source in {site.atom_uid for site in child.snapshot.sites}
    assert len(mutation.lineage) == len(model.snapshot.sites)


def test_model_mutation_rejects_stale_or_foreign_snapshot_selection(tmp_path: Path) -> None:
    _, service = _service(tmp_path)
    _, model = _graphene(service)
    child, _ = service.mutate_structure_model(
        source_variant_id=model.variant.id,
        source_snapshot_id=model.snapshot.id,
        variant_name="vacancy",
        vacancy_atom_uids=(model.snapshot.sites[0].atom_uid,),
    )

    with pytest.raises(ApplicationServiceError, match="stale"):
        service.mutate_structure_model(
            source_variant_id=model.variant.id,
            source_snapshot_id=child.snapshot.id,
            variant_name="invalid",
            vacancy_atom_uids=(child.snapshot.sites[0].atom_uid,),
        )


def test_single_metal_active_site_and_adsorbate_conformer_persist_exact_uids(
    tmp_path: Path,
) -> None:
    root, service = _service(tmp_path)
    _, model = _graphene(service)
    anchors = tuple(site.atom_uid for site in model.snapshot.sites[:3])

    metal = service.build_single_metal_model(
        source_variant_id=model.variant.id,
        source_snapshot_id=model.snapshot.id,
        variant_name="Fe-site",
        spec=SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=anchors,
            side=SiteSide.TOP,
            height_angstrom=1.8,
        ),
    )
    active_site = service.create_active_site_from_single_metal(
        variant=metal.variant,
        build=metal.build,
    )
    assert active_site.center_atom_uids == (metal.build.metal_atom_uid,)

    adsorbate = service.build_adsorbate_conformer(
        structure_variant_id=metal.variant.id,
        source_snapshot_id=metal.build.snapshot.id,
        active_site_id=active_site.id,
        state_label="*H",
        placement=AdsorbatePlacementSpec(
            template_key="H",
            target_center_atom_uids=active_site.center_atom_uids,
            binding_mode=BindingMode.SINGLE_CENTER,
            height_angstrom=1.5,
            contacts=(AdsorbateContactSpec("H", active_site.center_atom_uids[0]),),
        ),
        conformer_name="atop H",
        reaction_role="HER intermediate",
    )

    reopened = ProjectStore(root).open()
    assert active_site.id in {item.id for item in reopened.active_sites}
    assert adsorbate.state.id in {item.id for item in reopened.adsorption_states}
    assert adsorbate.conformer.id in {item.id for item in reopened.state_conformers}
    assert adsorbate.build.snapshot.id in {item.id for item in reopened.structure_snapshots}
    assert metal.variant.current_structure_snapshot_id == metal.build.snapshot.id
    persisted_variant = next(item for item in reopened.structure_variants if item.id == metal.variant.id)
    assert persisted_variant.current_structure_snapshot_id == metal.build.snapshot.id
    assert adsorbate.conformer.structure_snapshot_id == adsorbate.build.snapshot.id
    assert adsorbate.conformer.binding_edges[0].site_atom_uid == metal.build.metal_atom_uid


def test_multi_metal_child_variant_and_canonical_active_site(tmp_path: Path) -> None:
    _, service = _service(tmp_path)
    _, model = _graphene(service)
    first = tuple(site.atom_uid for site in model.snapshot.sites[:2])
    second = tuple(site.atom_uid for site in model.snapshot.sites[4:6])

    metals = service.build_multi_metal_model(
        source_variant_id=model.variant.id,
        source_snapshot_id=model.snapshot.id,
        variant_name="FeCo-site",
        spec=MultiMetalSiteSpec(
            centers=(
                MultiMetalCenterSpec("Fe", first, SiteSide.TOP, 1.7),
                MultiMetalCenterSpec("Co", second, SiteSide.TOP, 1.7),
            ),
            metal_metal_topology_intent="proximal",
        ),
    )
    active_site = service.create_active_site_from_multi_metal(
        variant=metals.variant,
        build=metals.build,
    )

    assert metals.variant.parent_variant_id == model.variant.id
    assert active_site.center_atom_uids == metals.build.metal_atom_uids
    assert len(active_site.center_atom_uids) == 2
