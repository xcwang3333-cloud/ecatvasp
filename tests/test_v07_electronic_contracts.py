from __future__ import annotations

import pytest

from ecatvasp.analysis import (
    CanonicalDosResult,
    DosSeries,
    ElectronicEnergyAxis,
    ExternalInputDigest,
    ExternalToolInvocation,
    OrbitalChannel,
    ProjectionScope,
    SpinChannel,
)
from ecatvasp.domain.ids import new_atom_uid, new_structure_snapshot_id


_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _axis() -> ElectronicEnergyAxis:
    return ElectronicEnergyAxis(
        energies_ev=(-6.0, -1.0, 2.0),
        fermi_energy_ev=-1.0,
    )


def test_energy_axis_retains_native_values_and_exposes_explicit_fermi_view() -> None:
    axis = _axis()

    assert axis.energies_ev == (-6.0, -1.0, 2.0)
    assert axis.relative_to_fermi() == (-5.0, 0.0, 3.0)


def test_canonical_dos_binds_atom_projection_to_permanent_uid() -> None:
    atom_uid = new_atom_uid()
    orbital = OrbitalChannel(label="dz2", angular_momentum=2)
    result = CanonicalDosResult(
        structure_snapshot_id=new_structure_snapshot_id(),
        energy_axis=_axis(),
        series=(
            DosSeries(
                scope=ProjectionScope.SYSTEM,
                spin=SpinChannel.UP,
                values=(1.0, 2.0, 1.5),
            ),
            DosSeries(
                scope=ProjectionScope.SYSTEM,
                spin=SpinChannel.DOWN,
                values=(0.8, 1.7, 1.2),
            ),
            DosSeries(
                scope=ProjectionScope.ATOM,
                spin=SpinChannel.UP,
                values=(0.2, 0.4, 0.3),
                atom_uid=atom_uid,
                element="Fe",
                orbital=orbital,
            ),
            DosSeries(
                scope=ProjectionScope.ATOM,
                spin=SpinChannel.DOWN,
                values=(0.1, 0.3, 0.2),
                atom_uid=atom_uid,
                element="Fe",
                orbital=orbital,
            ),
        ),
        atom_index_map_sha256=_HASH_A.upper(),
    )

    projected = tuple(item for item in result.series if item.scope is ProjectionScope.ATOM)
    assert {item.atom_uid for item in projected} == {atom_uid}
    assert result.atom_index_map_sha256 == _HASH_A
    assert result.content_hash == result.content_hash


def test_canonical_parsed_dos_rejects_element_aggregation() -> None:
    with pytest.raises(ValueError, match="derived aggregation"):
        CanonicalDosResult(
            structure_snapshot_id=new_structure_snapshot_id(),
            energy_axis=_axis(),
            series=(
                DosSeries(
                    scope=ProjectionScope.SYSTEM,
                    spin=SpinChannel.TOTAL,
                    values=(1.0, 2.0, 1.5),
                ),
                DosSeries(
                    scope=ProjectionScope.ELEMENT,
                    spin=SpinChannel.TOTAL,
                    values=(0.4, 0.8, 0.6),
                    element="C",
                ),
            ),
            atom_index_map_sha256=_HASH_A,
        )


def test_canonical_dos_rejects_projection_spin_schema_mismatch() -> None:
    with pytest.raises(ValueError, match="same spin schema"):
        CanonicalDosResult(
            structure_snapshot_id=new_structure_snapshot_id(),
            energy_axis=_axis(),
            series=(
                DosSeries(
                    scope=ProjectionScope.SYSTEM,
                    spin=SpinChannel.UP,
                    values=(1.0, 2.0, 1.5),
                ),
                DosSeries(
                    scope=ProjectionScope.SYSTEM,
                    spin=SpinChannel.DOWN,
                    values=(0.8, 1.7, 1.2),
                ),
                DosSeries(
                    scope=ProjectionScope.ATOM,
                    spin=SpinChannel.UP,
                    values=(0.2, 0.4, 0.3),
                    atom_uid=new_atom_uid(),
                    element="Pb",
                    orbital=OrbitalChannel(label="pz", angular_momentum=1),
                ),
            ),
            atom_index_map_sha256=_HASH_A,
        )


def test_external_tool_invocation_hash_covers_command_version_and_inputs() -> None:
    base = ExternalToolInvocation(
        tool="bader",
        tool_version="1.05",
        argv=("bader", "CHGCAR", "-ref", "CHGCAR_sum"),
        inputs=(
            ExternalInputDigest(role="charge_density", sha256=_HASH_A),
            ExternalInputDigest(role="all_electron_reference", sha256=_HASH_B),
        ),
    )
    changed = ExternalToolInvocation(
        tool="bader",
        tool_version="1.05",
        argv=("bader", "CHGCAR"),
        inputs=(
            ExternalInputDigest(role="charge_density", sha256=_HASH_A),
            ExternalInputDigest(role="all_electron_reference", sha256=_HASH_B),
        ),
    )

    assert base.provenance_hash != changed.provenance_hash


def test_external_tool_input_roles_are_unambiguous() -> None:
    with pytest.raises(ValueError, match="roles must be unique"):
        ExternalToolInvocation(
            tool="lobster",
            tool_version="5.1.0",
            argv=("lobster",),
            inputs=(
                ExternalInputDigest(role="wavefunction", sha256=_HASH_A),
                ExternalInputDigest(role="wavefunction", sha256=_HASH_B),
            ),
        )
