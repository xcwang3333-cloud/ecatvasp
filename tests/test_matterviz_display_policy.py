from __future__ import annotations

import pytest

from ecatvasp import domain, visualization


def _snapshot() -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=domain.Lattice(
            ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=(
            domain.StructureSite(
                domain.new_atom_uid(),
                "C",
                (0.5, 0.5, 0.5),
            ),
        ),
        origin=domain.StructureOrigin.BUILT,
        periodic=(True, True, True),
    )


def test_default_display_policy_locks_scientific_overlay_index_frame() -> None:
    bundle = visualization.build_matterviz_view(_snapshot())

    assert bundle.display_policy == visualization.MatterVizDisplayPolicy(
        cell_type="original",
        supercell_scaling="1x1x1",
        apply_supercell_scaling=False,
        show_image_atoms=False,
    )
    payload = bundle.to_dict()
    assert payload["display_policy"] == {
        "cell_type": "original",
        "supercell_scaling": "1x1x1",
        "apply_supercell_scaling": False,
        "show_image_atoms": False,
    }


@pytest.mark.parametrize(
    "policy",
    (
        visualization.MatterVizDisplayPolicy,
    ),
)
def test_display_policy_type_is_public(
    policy: type[visualization.MatterVizDisplayPolicy],
) -> None:
    assert policy().cell_type == "original"


def test_display_policy_rejects_viewer_transforms_that_renumber_sites() -> None:
    with pytest.raises(visualization.MatterVizAdapterError, match="original cell"):
        visualization.MatterVizDisplayPolicy(cell_type="primitive")
    with pytest.raises(visualization.MatterVizAdapterError, match="1x1x1"):
        visualization.MatterVizDisplayPolicy(
            supercell_scaling="2x2x1",
            apply_supercell_scaling=True,
        )
    with pytest.raises(visualization.MatterVizAdapterError, match="image atoms"):
        visualization.MatterVizDisplayPolicy(show_image_atoms=True)
