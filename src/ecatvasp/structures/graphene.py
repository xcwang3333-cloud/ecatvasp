"""Pristine single-layer graphene construction for Model Studio."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

from ecatvasp.domain import (
    Lattice,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)


@dataclass(frozen=True, slots=True)
class GrapheneBuildSpec:
    """Scientific inputs for a pristine periodic graphene supercell.

    ``bond_length_angstrom`` is the nearest-neighbor C-C distance. The graphene
    primitive lattice constant is therefore ``sqrt(3) * bond_length_angstrom``.
    ``vacuum_gap_angstrom`` is the separation between periodic graphene planes
    along z; for an ideal zero-thickness sheet it is also the c-axis length.
    """

    nx: int = 4
    ny: int = 4
    bond_length_angstrom: float = 1.42
    vacuum_gap_angstrom: float = 15.0
    label: str | None = "graphene"

    def __post_init__(self) -> None:
        _require_positive_repeat(self.nx, "nx")
        _require_positive_repeat(self.ny, "ny")
        _require_positive_finite(self.bond_length_angstrom, "bond_length_angstrom")
        _require_positive_finite(self.vacuum_gap_angstrom, "vacuum_gap_angstrom")
        if self.label is not None and not self.label.strip():
            raise ValueError("label must not be blank when defined")

    @property
    def primitive_lattice_constant_angstrom(self) -> float:
        """Return the in-plane graphene primitive lattice constant in angstrom."""

        return sqrt(3.0) * self.bond_length_angstrom

    @property
    def atom_count(self) -> int:
        """Return the number of carbon atoms in the requested supercell."""

        return 2 * self.nx * self.ny


def build_graphene(spec: GrapheneBuildSpec) -> StructureSnapshot:
    """Build one immutable pristine graphene supercell with fresh atom identities.

    The primitive cell uses two carbon sites at fractional in-plane coordinates
    (0, 0) and (1/3, 1/3) in a 60-degree hexagonal basis. Replication is explicit
    in ``nx`` and ``ny``. The graphene plane is centered at z = 1/2 and the
    resulting VASP-style slab cell remains periodic in all three directions.
    """

    lattice_constant = spec.primitive_lattice_constant_angstrom
    sqrt_three = sqrt(3.0)
    lattice = Lattice(
        vectors=(
            (spec.nx * lattice_constant, 0.0, 0.0),
            (
                0.5 * spec.ny * lattice_constant,
                0.5 * sqrt_three * spec.ny * lattice_constant,
                0.0,
            ),
            (0.0, 0.0, spec.vacuum_gap_angstrom),
        )
    )

    sites: list[StructureSite] = []
    basis = ((0.0, 0.0), (1.0 / 3.0, 1.0 / 3.0))
    for ix in range(spec.nx):
        for iy in range(spec.ny):
            for basis_x, basis_y in basis:
                sites.append(
                    StructureSite(
                        atom_uid=new_atom_uid(),
                        element="C",
                        fractional_coords=(
                            (ix + basis_x) / spec.nx,
                            (iy + basis_y) / spec.ny,
                            0.5,
                        ),
                    )
                )

    return StructureSnapshot(
        lattice=lattice,
        sites=tuple(sites),
        label=spec.label,
        origin=StructureOrigin.BUILT,
        periodic=(True, True, True),
    )


def _require_positive_repeat(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_positive_finite(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite positive number")
    if not isfinite(float(value)) or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive number")
