"""Acceptance tests for Pb1/Pb2 identity survival across POSCAR-like reorders."""

import pytest

from ecatvasp.domain import (
    ActiveSite,
    Lattice,
    SideLabel,
    SiteSide,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.domain.ids import StructureVariantId, new_structure_variant_id
from ecatvasp.structures import GeometrySite, reconcile_reordered_sites


@pytest.mark.parametrize("nuclearity", [1, 2])
def test_pb_identity_survives_poscar_reorder(nuclearity: int) -> None:
    pb_top = new_atom_uid()
    pb_bottom = new_atom_uid()
    carbon = new_atom_uid()
    nitrogen = new_atom_uid()
    sites = [
        StructureSite(carbon, "C", (0.50, 0.50, 0.50)),
        StructureSite(pb_top, "Pb", (0.48, 0.50, 0.58)),
        StructureSite(nitrogen, "N", (0.42, 0.50, 0.50)),
    ]
    side_labels = [SideLabel(pb_top, SiteSide.TOP)]
    center_uids = [pb_top]
    if nuclearity == 2:
        sites.append(StructureSite(pb_bottom, "Pb", (0.52, 0.50, 0.42)))
        side_labels.append(SideLabel(pb_bottom, SiteSide.BOTTOM))
        center_uids.append(pb_bottom)

    source = StructureSnapshot(
        lattice=Lattice(((12.0, 0.0, 0.0), (0.0, 12.0, 0.0), (0.0, 0.0, 20.0))),
        sites=tuple(sites),
        label=f"Pb{nuclearity} source",
    )
    variant_id: StructureVariantId = new_structure_variant_id()
    active_site = ActiveSite(
        structure_variant_id=variant_id,
        center_atom_uids=tuple(center_uids),
        topology="isolated" if nuclearity == 1 else "opposite-side",
        side_labels=tuple(side_labels),
    )

    order = (1, 2, 0) if nuclearity == 1 else (3, 0, 1, 2)
    reordered_geometry = tuple(
        GeometrySite(source.sites[index].element, source.sites[index].fractional_coords)
        for index in order
    )
    result = reconcile_reordered_sites(source, reordered_geometry, label="POSCAR reordered")

    target_indices = {
        site.atom_uid: index for index, site in enumerate(result.snapshot.sites)
    }
    source_indices = {site.atom_uid: index for index, site in enumerate(source.sites)}

    for atom_uid in active_site.center_atom_uids:
        assert result.snapshot.contains_atom(atom_uid)
        assert target_indices[atom_uid] != source_indices[atom_uid]
    assert active_site.nuclearity == nuclearity
    if nuclearity == 2:
        assert {label.side for label in active_site.side_labels} == {
            SiteSide.TOP,
            SiteSide.BOTTOM,
        }
