from __future__ import annotations

import pytest

from ecatvasp.domain import (
    ActiveSiteNuclearityClass,
    Catalyst,
    Lattice,
    Project,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    new_atom_uid,
)
from ecatvasp.structures import ActiveSiteToolingError, create_active_site


def _snapshot(center_count: int) -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((12.0, 0.0, 0.0), (0.0, 12.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=tuple(
            StructureSite(
                new_atom_uid(),
                "Fe",
                (0.20 + 0.12 * index, 0.50, 0.55),
            )
            for index in range(center_count)
        ),
        origin=StructureOrigin.BUILT,
    )


def _variant(snapshot: StructureSnapshot) -> StructureVariant:
    project = Project(name="Nuclearity semantics", slug="nuclearity-semantics")
    catalyst = Catalyst(project_id=project.id, name="site model", slug="site-model")
    return StructureVariant(
        catalyst_id=catalyst.id,
        name="active-site model",
        variant_type=VariantType.SITE_TOPOLOGY,
        current_structure_snapshot_id=snapshot.id,
    )


@pytest.mark.parametrize(
    ("center_count", "expected"),
    (
        (1, ActiveSiteNuclearityClass.SINGLE_CENTER),
        (2, ActiveSiteNuclearityClass.DUAL_CENTER),
        (3, ActiveSiteNuclearityClass.MULTI_CENTER),
        (6, ActiveSiteNuclearityClass.MULTI_CENTER),
    ),
)
def test_active_site_nuclearity_class_is_derived_from_explicit_centers(
    center_count: int,
    expected: ActiveSiteNuclearityClass,
) -> None:
    snapshot = _snapshot(center_count)
    variant = _variant(snapshot)
    site = create_active_site(
        variant=variant,
        snapshot=snapshot,
        center_atom_uids=tuple(item.atom_uid for item in snapshot.sites),
        topology="compact ensemble" if center_count > 2 else None,
    )

    assert site.nuclearity == center_count
    assert site.nuclearity_class is expected


def test_active_site_tooling_allows_cluster_scale_nuclearity() -> None:
    snapshot = _snapshot(6)
    variant = _variant(snapshot)

    site = create_active_site(
        variant=variant,
        snapshot=snapshot,
        center_atom_uids=tuple(item.atom_uid for item in snapshot.sites),
        topology="Cu6 cluster",
    )

    assert site.nuclearity == 6
    assert site.nuclearity_class is ActiveSiteNuclearityClass.MULTI_CENTER
    assert site.topology == "cu6-cluster"


def test_active_site_tooling_rejects_empty_center_set_at_boundary() -> None:
    snapshot = _snapshot(1)
    variant = _variant(snapshot)

    with pytest.raises(ActiveSiteToolingError, match="at least one center"):
        create_active_site(
            variant=variant,
            snapshot=snapshot,
            center_atom_uids=(),
        )
