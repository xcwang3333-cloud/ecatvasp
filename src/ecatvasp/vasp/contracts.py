"""Immutable preparation contracts for the v0.3 VASP input pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from ecatvasp.domain.ids import ProjectId
from ecatvasp.domain.method import KPointPolicy, canonical_sha256

ECATVASP_ECAT_STANDARD = "ECATVASP_ECAT_STANDARD"
ECATVASP_ECAT_STANDARD_EDIFFG_EV_PER_ANGSTROM = -0.02


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized


class VaspSystemKind(StrEnum):
    """Physical periodicity context used while resolving VASP inputs."""

    SLAB_2D = "slab_2d"
    MOLECULE_0D = "molecule_0d"
    PERIODIC_3D = "periodic_3d"


class LatticeAxis(StrEnum):
    """Lattice-vector axis used for slab vacuum and dipole policies."""

    A = "a"
    B = "b"
    C = "c"

    @property
    def index(self) -> int:
        """Return the zero-based lattice-vector index."""

        return {LatticeAxis.A: 0, LatticeAxis.B: 1, LatticeAxis.C: 2}[self]


@dataclass(frozen=True, slots=True)
class VaspSystemContext:
    """Explicit physical context that must not be guessed from atom types or cell shape."""

    kind: VaspSystemKind
    vacuum_axis: LatticeAxis | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if self.kind is VaspSystemKind.SLAB_2D and self.vacuum_axis is None:
            raise ValueError("SLAB_2D context requires an explicit vacuum_axis")
        if self.kind is not VaspSystemKind.SLAB_2D and self.vacuum_axis is not None:
            raise ValueError("vacuum_axis is only valid for SLAB_2D context")
        if self.label is not None:
            _require_text(self.label, "label")


@dataclass(frozen=True, slots=True)
class ProjectNumericalLock:
    """Method-aware project lock consumed before a final MethodFingerprint is built.

    The lock is a VASP preparation-layer policy object, not a frozen scientific-domain
    entity. Its effective ENCUT and k-point policy must be copied into the final
    ProtocolDefinition so they participate in MethodFingerprint identity.
    """

    project_id: ProjectId
    system_kind: VaspSystemKind
    core_method_hash: str
    encut_ev: float
    encut_validation_hash: str
    kpoints: KPointPolicy
    kpoints_validation_hash: str | None = None
    standard_name: str = ECATVASP_ECAT_STANDARD
    revision: int = 1

    def __post_init__(self) -> None:
        if not isfinite(self.encut_ev) or self.encut_ev <= 0:
            raise ValueError("encut_ev must be finite and positive")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        _require_text(self.standard_name, "standard_name")
        object.__setattr__(
            self,
            "core_method_hash",
            _normalized_sha256(self.core_method_hash, "core_method_hash"),
        )
        object.__setattr__(
            self,
            "encut_validation_hash",
            _normalized_sha256(self.encut_validation_hash, "encut_validation_hash"),
        )
        if self.kpoints_validation_hash is not None:
            object.__setattr__(
                self,
                "kpoints_validation_hash",
                _normalized_sha256(
                    self.kpoints_validation_hash,
                    "kpoints_validation_hash",
                ),
            )

    @property
    def lock_hash(self) -> str:
        """Return a deterministic digest for provenance and preparation manifests."""

        return canonical_sha256(self)
