"""Deterministic VASP k-point planning for explicit meshes and KSPACING."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from math import acos, ceil, floor, isfinite, pi, sqrt

from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.ids import StructureSnapshotId
from ecatvasp.domain.method import (
    KPointPolicy,
    KPointPolicyKind,
    ParameterEntry,
    ProtocolDefinition,
    canonical_sha256,
)
from ecatvasp.vasp.contracts import (
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)

ECATVASP_KPOINT_CENTERING = "ECATVASP_KPOINT_CENTERING"


class KPointPreparationError(ValueError):
    """Raised when a k-point policy cannot be materialized without guessing."""


class KPointCentering(StrEnum):
    """Automatic-mesh centering used by VASP."""

    GAMMA = "gamma"
    MONKHORST_PACK = "monkhorst_pack"


@dataclass(frozen=True, slots=True)
class KPointValidationEvidence:
    """Completed solid-state convergence evidence for one selected k-point plan."""

    core_method_hash: str
    system_kind: VaspSystemKind
    tested_plan_hashes: tuple[str, ...]
    selected_plan_hash: str
    analysis_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "core_method_hash",
            _normalized_sha256(self.core_method_hash, "core_method_hash"),
        )
        if not self.tested_plan_hashes:
            raise ValueError("tested_plan_hashes must not be empty")
        tested = tuple(
            sorted(
                _normalized_sha256(value, "tested_plan_hash")
                for value in self.tested_plan_hashes
            )
        )
        if len(tested) != len(set(tested)):
            raise ValueError("tested k-point plan hashes must be unique")
        object.__setattr__(self, "tested_plan_hashes", tested)
        object.__setattr__(
            self,
            "selected_plan_hash",
            _normalized_sha256(self.selected_plan_hash, "selected_plan_hash"),
        )
        object.__setattr__(
            self,
            "analysis_hash",
            _normalized_sha256(self.analysis_hash, "analysis_hash"),
        )
        if self.selected_plan_hash not in tested:
            raise ValueError("selected_plan_hash must be one of the tested k-point plans")

    @property
    def evidence_hash(self) -> str:
        """Return a deterministic digest for manifest/provenance use."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class PreparedKPoints:
    """Immutable deterministic k-point preparation result.

    ``mesh`` is the actual explicit mesh for KPOINTS-backed policies and the
    mesh predicted from VASP's KSPACING formula for KSPACING policies.
    """

    structure_snapshot_id: StructureSnapshotId
    policy: KPointPolicy
    system_context: VaspSystemContext
    centering: KPointCentering
    mesh: tuple[int, int, int]
    text: str | None
    sha256: str | None
    kspacing_inv_angstrom: float | None = None
    kgamma: bool | None = None
    identity_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if any(component < 1 for component in self.mesh):
            raise ValueError("prepared k-point mesh components must be positive")
        if self.policy.kind is KPointPolicyKind.KSPACING:
            if self.text is not None or self.sha256 is not None:
                raise ValueError("KSPACING preparation must not materialize a KPOINTS file")
            if self.kspacing_inv_angstrom != self.policy.value:
                raise ValueError("prepared KSPACING must equal KPointPolicy.value")
            if self.kgamma is None:
                raise ValueError("KSPACING preparation requires explicit KGAMMA")
        else:
            if self.text is None or self.sha256 is None:
                raise ValueError("KPOINTS-backed policies require text and sha256")
            expected_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
            if self.sha256 != expected_hash:
                raise ValueError("KPOINTS sha256 does not match text content")
            if self.kspacing_inv_angstrom is not None or self.kgamma is not None:
                raise ValueError("KPOINTS-backed policies must not carry KSPACING/KGAMMA")

        object.__setattr__(
            self,
            "identity_hash",
            canonical_sha256(
                {
                    "policy": self.policy,
                    "system_kind": self.system_context.kind,
                    "vacuum_axis": self.system_context.vacuum_axis,
                    "centering": self.centering,
                    "mesh": self.mesh,
                    "text_sha256": self.sha256,
                    "kspacing_inv_angstrom": self.kspacing_inv_angstrom,
                    "kgamma": self.kgamma,
                }
            ),
        )

    @property
    def uses_kpoints_file(self) -> bool:
        """Return whether this plan materializes an explicit KPOINTS file."""

        return self.text is not None

    @property
    def protocol_centering_parameter(self) -> ParameterEntry:
        """Return the namespaced Protocol parameter that fingerprints centering."""

        return ParameterEntry(ECATVASP_KPOINT_CENTERING, self.centering.value)


def prepare_kpoints(
    snapshot: StructureSnapshot,
    *,
    policy: KPointPolicy,
    system_context: VaspSystemContext,
    centering: KPointCentering | None = None,
) -> PreparedKPoints:
    """Resolve a deterministic VASP k-point plan without hidden policy inference.

    ``RECIPROCAL_DENSITY.value`` uses k-points per inverse-angstrom cubed of
    reciprocal-cell volume, matching the established ``kppvol`` meaning. Slab
    density generation always fixes the declared vacuum axis to one.
    """

    _require_non_singular_cell(snapshot)
    resolved_centering = _resolve_centering(
        snapshot=snapshot,
        policy=policy,
        centering=centering,
    )

    if system_context.kind is VaspSystemKind.MOLECULE_0D:
        if policy.kind is not KPointPolicyKind.GAMMA_ONLY:
            raise KPointPreparationError(
                "MOLECULE_0D requires the canonical GAMMA_ONLY k-point policy"
            )
        return _prepared_mesh_file(
            snapshot=snapshot,
            policy=policy,
            system_context=system_context,
            centering=resolved_centering,
            mesh=(1, 1, 1),
            comment="ECatVASP Gamma-only mesh",
        )

    if policy.kind is KPointPolicyKind.GAMMA_ONLY:
        return _prepared_mesh_file(
            snapshot=snapshot,
            policy=policy,
            system_context=system_context,
            centering=resolved_centering,
            mesh=(1, 1, 1),
            comment="ECatVASP Gamma-only mesh",
        )

    if policy.kind is KPointPolicyKind.EXPLICIT_MESH:
        assert policy.mesh is not None
        _validate_slab_vacuum_axis(policy.mesh, system_context)
        return _prepared_mesh_file(
            snapshot=snapshot,
            policy=policy,
            system_context=system_context,
            centering=resolved_centering,
            mesh=policy.mesh,
            comment="ECatVASP explicit mesh",
        )

    if policy.kind is KPointPolicyKind.RECIPROCAL_DENSITY:
        assert policy.value is not None
        mesh = _mesh_from_reciprocal_density(
            snapshot=snapshot,
            density=policy.value,
        )
        if system_context.kind is VaspSystemKind.SLAB_2D:
            assert system_context.vacuum_axis is not None
            mutable_mesh = list(mesh)
            mutable_mesh[system_context.vacuum_axis.axis_index] = 1
            mesh = (mutable_mesh[0], mutable_mesh[1], mutable_mesh[2])
        _validate_slab_vacuum_axis(mesh, system_context)
        return _prepared_mesh_file(
            snapshot=snapshot,
            policy=policy,
            system_context=system_context,
            centering=resolved_centering,
            mesh=mesh,
            comment=f"ECatVASP reciprocal density {policy.value:.12g}",
        )

    if policy.kind is KPointPolicyKind.KSPACING:
        assert policy.value is not None
        mesh = _predict_vasp_kspacing_mesh(snapshot=snapshot, kspacing=policy.value)
        _validate_slab_vacuum_axis(mesh, system_context)
        return PreparedKPoints(
            structure_snapshot_id=snapshot.id,
            policy=policy,
            system_context=system_context,
            centering=resolved_centering,
            mesh=mesh,
            text=None,
            sha256=None,
            kspacing_inv_angstrom=policy.value,
            kgamma=resolved_centering is KPointCentering.GAMMA,
        )

    raise KPointPreparationError(f"unsupported k-point policy: {policy.kind.value}")


def validate_protocol_kpoint_contract(
    *,
    protocol: ProtocolDefinition,
    prepared: PreparedKPoints,
) -> None:
    """Require the final Protocol identity to retain exact k-point semantics."""

    if protocol.kpoints != prepared.policy:
        raise KPointPreparationError(
            "ProtocolDefinition.kpoints does not match the prepared k-point policy"
        )
    matches = tuple(
        item
        for item in protocol.extra_parameters
        if item.name == ECATVASP_KPOINT_CENTERING
    )
    if len(matches) != 1:
        raise KPointPreparationError(
            "ProtocolDefinition requires exactly one ECATVASP_KPOINT_CENTERING parameter"
        )
    if matches[0].value != prepared.centering.value:
        raise KPointPreparationError(
            "ProtocolDefinition k-point centering does not match the prepared plan"
        )


def validate_project_lock_kpoints(
    *,
    lock: ProjectNumericalLock,
    prepared: PreparedKPoints,
    evidence: KPointValidationEvidence | None,
) -> None:
    """Require production k-point preparation to inherit a validated project lock."""

    if lock.system_kind is not prepared.system_context.kind:
        raise KPointPreparationError(
            "project lock system kind does not match the prepared k-point context"
        )
    if lock.kpoints != prepared.policy:
        raise KPointPreparationError(
            "project lock k-point policy does not match the prepared k-point policy"
        )

    if prepared.system_context.kind is VaspSystemKind.MOLECULE_0D:
        if evidence is None and lock.kpoints_validation_hash is None:
            return
        if evidence is None:
            raise KPointPreparationError(
                "molecule k-point validation hash is present without matching evidence"
            )
    elif evidence is None:
        raise KPointPreparationError(
            "solid production k-point lock requires convergence evidence"
        )

    assert evidence is not None
    if lock.core_method_hash != evidence.core_method_hash:
        raise KPointPreparationError(
            "k-point evidence core method does not match the project lock"
        )
    if evidence.system_kind is not prepared.system_context.kind:
        raise KPointPreparationError(
            "k-point evidence system kind does not match the prepared context"
        )
    if evidence.selected_plan_hash != prepared.identity_hash:
        raise KPointPreparationError(
            "k-point evidence selected plan does not match the prepared k-point plan"
        )
    if lock.kpoints_validation_hash != evidence.analysis_hash:
        raise KPointPreparationError(
            "project lock k-point validation hash does not match evidence"
        )


def validate_kpoints_file_presence(
    *,
    prepared: PreparedKPoints,
    kpoints_file_present: bool,
) -> None:
    """Fail closed on KSPACING/KPOINTS materialization conflicts."""

    if prepared.uses_kpoints_file == kpoints_file_present:
        return
    if prepared.policy.kind is KPointPolicyKind.KSPACING:
        raise KPointPreparationError(
            "KSPACING policy conflicts with a materialized KPOINTS file"
        )
    raise KPointPreparationError(
        "KPOINTS-backed policy requires exactly one materialized KPOINTS file"
    )


def _prepared_mesh_file(
    *,
    snapshot: StructureSnapshot,
    policy: KPointPolicy,
    system_context: VaspSystemContext,
    centering: KPointCentering,
    mesh: tuple[int, int, int],
    comment: str,
) -> PreparedKPoints:
    text = _serialize_mesh(mesh, centering, comment)
    return PreparedKPoints(
        structure_snapshot_id=snapshot.id,
        policy=policy,
        system_context=system_context,
        centering=centering,
        mesh=mesh,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _resolve_centering(
    *,
    snapshot: StructureSnapshot,
    policy: KPointPolicy,
    centering: KPointCentering | None,
) -> KPointCentering:
    if policy.kind is KPointPolicyKind.GAMMA_ONLY:
        if centering is not None and centering is not KPointCentering.GAMMA:
            raise KPointPreparationError("GAMMA_ONLY cannot use Monkhorst-Pack centering")
        return KPointCentering.GAMMA

    if centering is None:
        raise KPointPreparationError(
            "k-point centering must be explicit for non-GAMMA_ONLY policies"
        )
    if centering is KPointCentering.MONKHORST_PACK and _is_hexagonal_lattice(snapshot):
        raise KPointPreparationError(
            "hexagonal lattices require Gamma centering for symmetry-safe meshes"
        )
    return centering


def _validate_slab_vacuum_axis(
    mesh: tuple[int, int, int],
    system_context: VaspSystemContext,
) -> None:
    if system_context.kind is not VaspSystemKind.SLAB_2D:
        return
    assert system_context.vacuum_axis is not None
    if mesh[system_context.vacuum_axis.axis_index] != 1:
        raise KPointPreparationError(
            "SLAB_2D k-point mesh must be one along the declared vacuum axis"
        )


def _mesh_from_reciprocal_density(
    *,
    snapshot: StructureSnapshot,
    density: float,
) -> tuple[int, int, int]:
    if not isfinite(density) or density <= 0:
        raise KPointPreparationError("reciprocal density must be finite and positive")

    a, b, c = snapshot.lattice.vectors
    volume = abs(_determinant((a, b, c)))
    reciprocal_volume = (2 * pi) ** 3 / volume
    ngrid = density * reciprocal_volume
    lengths = (_norm(a), _norm(b), _norm(c))
    multiplier = (ngrid * lengths[0] * lengths[1] * lengths[2]) ** (1 / 3)
    return (
        floor(max(multiplier / lengths[0], 1.0)),
        floor(max(multiplier / lengths[1], 1.0)),
        floor(max(multiplier / lengths[2], 1.0)),
    )


def _predict_vasp_kspacing_mesh(
    *,
    snapshot: StructureSnapshot,
    kspacing: float,
) -> tuple[int, int, int]:
    if not isfinite(kspacing) or kspacing <= 0:
        raise KPointPreparationError("KSPACING must be finite and positive")
    reciprocal = _reciprocal_vectors_without_2pi(snapshot)
    return (
        max(1, ceil((2 * pi) * _norm(reciprocal[0]) / kspacing)),
        max(1, ceil((2 * pi) * _norm(reciprocal[1]) / kspacing)),
        max(1, ceil((2 * pi) * _norm(reciprocal[2]) / kspacing)),
    )


def _serialize_mesh(
    mesh: tuple[int, int, int],
    centering: KPointCentering,
    comment: str,
) -> str:
    style = "Gamma" if centering is KPointCentering.GAMMA else "Monkhorst-Pack"
    return (
        f"{comment}\n"
        "0\n"
        f"{style}\n"
        f"{mesh[0]} {mesh[1]} {mesh[2]}\n"
        "0 0 0\n"
    )


def _require_non_singular_cell(snapshot: StructureSnapshot) -> None:
    volume = _determinant(snapshot.lattice.vectors)
    if not isfinite(volume) or abs(volume) <= 1e-12:
        raise KPointPreparationError("k-point preparation requires a non-singular cell")


def _reciprocal_vectors_without_2pi(
    snapshot: StructureSnapshot,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    a, b, c = snapshot.lattice.vectors
    volume = _determinant((a, b, c))
    if not isfinite(volume) or abs(volume) <= 1e-12:
        raise KPointPreparationError("k-point preparation requires a non-singular cell")
    return (
        _scale(_cross(b, c), 1.0 / volume),
        _scale(_cross(c, a), 1.0 / volume),
        _scale(_cross(a, b), 1.0 / volume),
    )


def _is_hexagonal_lattice(snapshot: StructureSnapshot) -> bool:
    vectors = snapshot.lattice.vectors
    lengths = tuple(_norm(vector) for vector in vectors)
    for first_index, second_index, third_index in ((0, 1, 2), (0, 2, 1), (1, 2, 0)):
        first = vectors[first_index]
        second = vectors[second_index]
        third = vectors[third_index]
        first_length = lengths[first_index]
        second_length = lengths[second_index]
        third_length = lengths[third_index]
        if min(first_length, second_length, third_length) <= 0:
            continue
        if abs(first_length - second_length) > 1e-5 * max(first_length, second_length):
            continue
        if abs(_dot(first, third)) > 1e-5 * first_length * third_length:
            continue
        if abs(_dot(second, third)) > 1e-5 * second_length * third_length:
            continue
        angle = _angle_degrees(first, second)
        if min(abs(angle - 60.0), abs(angle - 120.0)) <= 1e-4:
            return True
    return False


def _angle_degrees(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    denominator = _norm(first) * _norm(second)
    cosine = max(-1.0, min(1.0, _dot(first, second) / denominator))
    return acos(cosine) * 180.0 / pi


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(first, second, strict=True))


def _cross(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _scale(
    vector: tuple[float, float, float],
    factor: float,
) -> tuple[float, float, float]:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _norm(vector: tuple[float, float, float]) -> float:
    return sqrt(sum(value * value for value in vector))


def _determinant(
    vectors: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> float:
    a, b, c = vectors
    return _dot(a, _cross(b, c))


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized
