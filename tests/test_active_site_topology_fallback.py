from __future__ import annotations

from ecatvasp.domain import (
    Catalyst,
    Lattice,
    Project,
    SiteSide,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    new_atom_uid,
)
from ecatvasp.structures import (
    MultiMetalCenterSpec,
    MultiMetalSiteSpec,
    active_site_from_multi_metal,
    build_multi_metal_site,
)


def test_multi_adapter_derives_opposite_side_topology_without_explicit_intent() -> None:
    source = StructureSnapshot(
        lattice=Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=(
            StructureSite(new_atom_uid(), "N", (0.40, 0.40, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.60, 0.40, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.40, 0.60, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.60, 0.60, 0.50)),
        ),
        origin=StructureOrigin.BUILT,
    )
    anchors = tuple(site.atom_uid for site in source.sites)
    result = build_multi_metal_site(
        source,
        MultiMetalSiteSpec(
            centers=(
                MultiMetalCenterSpec("Pb", anchors, SiteSide.TOP, 1.5),
                MultiMetalCenterSpec("Pb", anchors, SiteSide.BOTTOM, 1.5),
            )
        ),
    )
    project = Project(name="Pb2 topology", slug="pb2-topology")
    catalyst = Catalyst(project_id=project.id, name="Pb2", slug="pb2")
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="opposite-side Pb2",
        variant_type=VariantType.SITE_TOPOLOGY,
        current_structure_snapshot_id=result.snapshot.id,
    )

    active_site = active_site_from_multi_metal(variant=variant, result=result)

    assert active_site.topology == "opposite-side"
    assert tuple(label.side for label in active_site.side_labels) == (
        SiteSide.TOP,
        SiteSide.BOTTOM,
    )
