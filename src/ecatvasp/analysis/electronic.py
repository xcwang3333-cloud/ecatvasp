"""Canonical electronic-structure analysis value contracts for v0.7."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from ecatvasp.domain import canonical_sha256
from ecatvasp.domain.ids import AtomUid, StructureSnapshotId


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized


class ElectronicEnergyReference(StrEnum):
    """Energy-reference semantics for normalized electronic-structure data."""

    VASP_NATIVE = "vasp_native"


class SpinChannel(StrEnum):
    """Normalized collinear spin channels supported by the v0.7 DOS contract."""

    TOTAL = "total"
    UP = "up"
    DOWN = "down"


class ProjectionScope(StrEnum):
    """Scientific scope represented by one DOS series."""

    SYSTEM = "system"
    ATOM = "atom"
    ELEMENT = "element"


@dataclass(frozen=True, slots=True)
class OrbitalChannel:
    """Canonical orbital label without imposing one parser's column convention."""

    label: str
    angular_momentum: int

    def __post_init__(self) -> None:
        _require_text(self.label, "label")
        if any(character.isspace() for character in self.label):
            raise ValueError("orbital label must not contain whitespace")
        if self.angular_momentum < 0:
            raise ValueError("angular_momentum must not be negative")


@dataclass(frozen=True, slots=True)
class ElectronicEnergyAxis:
    """Native VASP energy grid plus an explicit Fermi level in the same reference frame."""

    energies_ev: tuple[float, ...]
    fermi_energy_ev: float
    reference: ElectronicEnergyReference = ElectronicEnergyReference.VASP_NATIVE

    def __post_init__(self) -> None:
        if len(self.energies_ev) < 2:
            raise ValueError("electronic energy axis requires at least two points")
        if not isfinite(self.fermi_energy_ev):
            raise ValueError("fermi_energy_ev must be finite")
        if not all(isfinite(value) for value in self.energies_ev):
            raise ValueError("energies_ev must contain only finite values")
        pairs = zip(self.energies_ev, self.energies_ev[1:], strict=False)
        if any(right <= left for left, right in pairs):
            raise ValueError("energies_ev must be strictly increasing")

    def relative_to_fermi(self) -> tuple[float, ...]:
        """Return an explicit E-E_F view without mutating the canonical native energy axis."""

        return tuple(value - self.fermi_energy_ev for value in self.energies_ev)


@dataclass(frozen=True, slots=True)
class DosSeries:
    """One total or projected density-of-states series on a shared energy axis."""

    scope: ProjectionScope
    spin: SpinChannel
    values: tuple[float, ...]
    atom_uid: AtomUid | None = None
    element: str | None = None
    orbital: OrbitalChannel | None = None

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("DOS series must contain at least one value")
        if not all(isfinite(value) for value in self.values):
            raise ValueError("DOS values must be finite")

        if self.scope is ProjectionScope.SYSTEM:
            if self.atom_uid is not None or self.element is not None or self.orbital is not None:
                raise ValueError("system DOS must not carry atom, element, or orbital selectors")
            return

        if self.scope is ProjectionScope.ATOM:
            if self.atom_uid is None or self.element is None:
                raise ValueError("atom-projected DOS requires atom_uid and element")
            _require_text(self.element, "element")
            return

        if self.scope is ProjectionScope.ELEMENT:
            if self.atom_uid is not None or self.element is None:
                raise ValueError("element-projected DOS requires element and forbids atom_uid")
            _require_text(self.element, "element")

    @property
    def semantic_key(self) -> tuple[object, ...]:
        """Return the projection identity excluding numerical values."""

        return (self.scope, self.spin, self.atom_uid, self.element, self.orbital)


@dataclass(frozen=True, slots=True)
class CanonicalDosResult:
    """Parsed DOS/PDOS facts bound to one immutable structure and frozen atom-index map."""

    structure_snapshot_id: StructureSnapshotId
    energy_axis: ElectronicEnergyAxis
    series: tuple[DosSeries, ...]
    atom_index_map_sha256: str
    contract_version: int = 1

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValueError("unsupported canonical DOS contract version")
        if self.energy_axis.reference is not ElectronicEnergyReference.VASP_NATIVE:
            raise ValueError("canonical parsed DOS must retain the native VASP energy reference")
        object.__setattr__(
            self,
            "atom_index_map_sha256",
            _normalized_sha256(self.atom_index_map_sha256, "atom_index_map_sha256"),
        )
        if not self.series:
            raise ValueError("canonical DOS result requires at least one series")
        if any(len(item.values) != len(self.energy_axis.energies_ev) for item in self.series):
            raise ValueError("every DOS series must use the canonical energy grid")

        keys = tuple(item.semantic_key for item in self.series)
        if len(keys) != len(set(keys)):
            raise ValueError("DOS series semantic keys must be unique")
        if any(item.scope is ProjectionScope.ELEMENT for item in self.series):
            raise ValueError(
                "element-projected DOS is derived aggregation, not a canonical parsed DOS fact"
            )

        system_series = tuple(item for item in self.series if item.scope is ProjectionScope.SYSTEM)
        if not system_series:
            raise ValueError("canonical DOS result requires system-level DOS")
        system_spins = frozenset(item.spin for item in system_series)
        valid_spin_sets = (
            frozenset({SpinChannel.TOTAL}),
            frozenset({SpinChannel.UP, SpinChannel.DOWN}),
        )
        if system_spins not in valid_spin_sets or len(system_series) != len(system_spins):
            raise ValueError("system DOS must be either TOTAL or an UP/DOWN pair")

        projected = tuple(item for item in self.series if item.scope is ProjectionScope.ATOM)
        grouped_spins: dict[tuple[object, ...], set[SpinChannel]] = {}
        for item in projected:
            projection_key = (item.atom_uid, item.element, item.orbital)
            grouped_spins.setdefault(projection_key, set()).add(item.spin)
        if any(frozenset(spins) != system_spins for spins in grouped_spins.values()):
            raise ValueError(
                "each atom/orbital projection must use the same spin schema as system DOS"
            )

    @property
    def content_hash(self) -> str:
        """Return deterministic scientific content identity for the normalized dataset."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ExternalInputDigest:
    """Content digest for one logical input consumed by an external analysis tool."""

    role: str
    sha256: str

    def __post_init__(self) -> None:
        _require_text(self.role, "role")
        object.__setattr__(self, "sha256", _normalized_sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class ExternalToolInvocation:
    """Reproducible external-tool invocation provenance without scheduler/runtime identity."""

    tool: str
    tool_version: str
    argv: tuple[str, ...]
    inputs: tuple[ExternalInputDigest, ...]

    def __post_init__(self) -> None:
        _require_text(self.tool, "tool")
        _require_text(self.tool_version, "tool_version")
        if not self.argv:
            raise ValueError("external-tool argv must not be empty")
        for index, argument in enumerate(self.argv):
            _require_text(argument, f"argv[{index}]")
        if not self.inputs:
            raise ValueError("external-tool provenance requires at least one input digest")
        roles = tuple(item.role for item in self.inputs)
        if len(roles) != len(set(roles)):
            raise ValueError("external-tool input roles must be unique")

    @property
    def provenance_hash(self) -> str:
        """Return deterministic identity for tool version, command, and exact input contents."""

        return canonical_sha256(self)
