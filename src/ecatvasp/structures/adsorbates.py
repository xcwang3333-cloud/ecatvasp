"""Canonical seed geometries for electrocatalysis adsorbate construction."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

Cartesian3 = tuple[float, float, float]


class AdsorbateTemplateError(ValueError):
    """Raised when an adsorbate template or library lookup is invalid."""


@dataclass(frozen=True, slots=True)
class AdsorbateAtomTemplate:
    """One template-local atom without a scientific atom_uid."""

    key: str
    element: str
    cartesian_coords: Cartesian3

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("adsorbate atom key must not be blank")
        if not self.element.strip():
            raise ValueError("adsorbate atom element must not be blank")
        if not all(isfinite(component) for component in self.cartesian_coords):
            raise ValueError("adsorbate atom coordinates must be finite")


@dataclass(frozen=True, slots=True)
class AdsorbateTemplate:
    """A reusable local-coordinate seed geometry independent of atom identity."""

    key: str
    atoms: tuple[AdsorbateAtomTemplate, ...]
    primary_anchor_atom_key: str
    anchor_atom_keys: tuple[str, ...]
    orientation_reference_atom_key: str | None
    reaction_families: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("adsorbate template key must not be blank")
        if self.key.startswith("*"):
            raise ValueError("adsorbate template key must not include adsorption-state '*'")
        if not self.atoms:
            raise ValueError("adsorbate template requires at least one atom")

        atom_keys = tuple(atom.key for atom in self.atoms)
        if len(atom_keys) != len(set(atom_keys)):
            raise ValueError("adsorbate template atom keys must be unique")
        if self.primary_anchor_atom_key not in atom_keys:
            raise ValueError("primary anchor atom key must reference a template atom")
        if not self.anchor_atom_keys:
            raise ValueError("adsorbate template requires at least one eligible anchor atom")
        if len(self.anchor_atom_keys) != len(set(self.anchor_atom_keys)):
            raise ValueError("anchor atom keys must be unique")
        if any(key not in atom_keys for key in self.anchor_atom_keys):
            raise ValueError("anchor atom keys must reference template atoms")
        if self.primary_anchor_atom_key not in self.anchor_atom_keys:
            raise ValueError("primary anchor atom must be an eligible anchor atom")
        if self.orientation_reference_atom_key is not None:
            if self.orientation_reference_atom_key not in atom_keys:
                raise ValueError("orientation reference must reference a template atom")
            if self.orientation_reference_atom_key == self.primary_anchor_atom_key:
                raise ValueError("orientation reference must differ from the primary anchor")
        if not self.reaction_families or any(
            not family.strip() for family in self.reaction_families
        ):
            raise ValueError("reaction_families must contain nonblank values")

        for left_index, left in enumerate(self.atoms):
            for right in self.atoms[left_index + 1 :]:
                if _distance(left.cartesian_coords, right.cartesian_coords) <= 1.0e-9:
                    raise ValueError("adsorbate template atoms must not overlap")

    def atom(self, atom_key: str) -> AdsorbateAtomTemplate:
        """Resolve one template atom by its local key."""

        for atom in self.atoms:
            if atom.key == atom_key:
                return atom
        raise AdsorbateTemplateError(
            f"adsorbate template {self.key!r} has no atom key {atom_key!r}"
        )

    @property
    def atom_keys(self) -> tuple[str, ...]:
        return tuple(atom.key for atom in self.atoms)


def _atom(key: str, element: str, xyz: Cartesian3) -> AdsorbateAtomTemplate:
    return AdsorbateAtomTemplate(key=key, element=element, cartesian_coords=xyz)


_BUILTIN_TEMPLATES: tuple[AdsorbateTemplate, ...] = (
    AdsorbateTemplate(
        key="H",
        atoms=(_atom("H", "H", (0.0, 0.0, 0.0)),),
        primary_anchor_atom_key="H",
        anchor_atom_keys=("H",),
        orientation_reference_atom_key=None,
        reaction_families=("HER",),
    ),
    AdsorbateTemplate(
        key="O",
        atoms=(_atom("O", "O", (0.0, 0.0, 0.0)),),
        primary_anchor_atom_key="O",
        anchor_atom_keys=("O",),
        orientation_reference_atom_key=None,
        reaction_families=("ORR", "OER"),
    ),
    AdsorbateTemplate(
        key="OH",
        atoms=(
            _atom("O", "O", (0.0, 0.0, 0.0)),
            _atom("H", "H", (0.97, 0.0, 0.0)),
        ),
        primary_anchor_atom_key="O",
        anchor_atom_keys=("O",),
        orientation_reference_atom_key="H",
        reaction_families=("ORR", "OER"),
    ),
    AdsorbateTemplate(
        key="OOH",
        atoms=(
            _atom("O_anchor", "O", (0.0, 0.0, 0.0)),
            _atom("O_terminal", "O", (1.45, 0.0, 0.0)),
            _atom("H", "H", (1.75, 0.88, 0.0)),
        ),
        primary_anchor_atom_key="O_anchor",
        anchor_atom_keys=("O_anchor", "O_terminal"),
        orientation_reference_atom_key="O_terminal",
        reaction_families=("ORR", "OER"),
    ),
    AdsorbateTemplate(
        key="O2",
        atoms=(
            _atom("O1", "O", (0.0, 0.0, 0.0)),
            _atom("O2", "O", (1.21, 0.0, 0.0)),
        ),
        primary_anchor_atom_key="O1",
        anchor_atom_keys=("O1", "O2"),
        orientation_reference_atom_key="O2",
        reaction_families=("ORR", "OER"),
    ),
    AdsorbateTemplate(
        key="CO2",
        atoms=(
            _atom("C", "C", (0.0, 0.0, 0.0)),
            _atom("O1", "O", (1.16, 0.0, 0.0)),
            _atom("O2", "O", (-1.16, 0.0, 0.0)),
        ),
        primary_anchor_atom_key="C",
        anchor_atom_keys=("C", "O1", "O2"),
        orientation_reference_atom_key="O1",
        reaction_families=("CO2RR",),
    ),
    AdsorbateTemplate(
        key="COOH",
        atoms=(
            _atom("C", "C", (0.0, 0.0, 0.0)),
            _atom("O_carbonyl", "O", (1.23, 0.0, 0.0)),
            _atom("O_hydroxyl", "O", (-0.60, 1.15, 0.0)),
            _atom("H", "H", (-1.50, 0.95, 0.0)),
        ),
        primary_anchor_atom_key="C",
        anchor_atom_keys=("C", "O_carbonyl", "O_hydroxyl"),
        orientation_reference_atom_key="O_carbonyl",
        reaction_families=("CO2RR",),
    ),
    AdsorbateTemplate(
        key="CO",
        atoms=(
            _atom("C", "C", (0.0, 0.0, 0.0)),
            _atom("O", "O", (1.14, 0.0, 0.0)),
        ),
        primary_anchor_atom_key="C",
        anchor_atom_keys=("C", "O"),
        orientation_reference_atom_key="O",
        reaction_families=("CO2RR",),
    ),
    AdsorbateTemplate(
        key="OCHO",
        atoms=(
            _atom("C", "C", (0.0, 0.0, 0.0)),
            _atom("O1", "O", (1.25, 0.35, 0.0)),
            _atom("O2", "O", (-1.25, 0.35, 0.0)),
            _atom("H", "H", (0.0, -1.10, 0.0)),
        ),
        primary_anchor_atom_key="O1",
        anchor_atom_keys=("O1", "O2", "C"),
        orientation_reference_atom_key="C",
        reaction_families=("CO2RR",),
    ),
)

_LIBRARY = {template.key: template for template in _BUILTIN_TEMPLATES}


def list_adsorbate_templates() -> tuple[AdsorbateTemplate, ...]:
    """Return the built-in v0.2 electrocatalysis adsorbate templates."""

    return _BUILTIN_TEMPLATES


def get_adsorbate_template(key: str) -> AdsorbateTemplate:
    """Resolve a built-in template by canonical chemical key."""

    stripped = key.strip()
    if not stripped:
        raise AdsorbateTemplateError("adsorbate template key must not be blank")
    if stripped.startswith("*"):
        raise AdsorbateTemplateError(
            "adsorbate template keys describe molecules/fragments and must not start with '*'"
        )
    normalized = stripped.upper()
    try:
        return _LIBRARY[normalized]
    except KeyError as exc:
        raise AdsorbateTemplateError(f"unknown adsorbate template {key!r}") from exc


def _distance(left: Cartesian3, right: Cartesian3) -> float:
    dx = left[0] - right[0]
    dy = left[1] - right[1]
    dz = left[2] - right[2]
    return sqrt(dx * dx + dy * dy + dz * dz)
