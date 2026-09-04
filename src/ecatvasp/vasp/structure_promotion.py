"""CONTCAR reconstruction and explicit structure promotion for v0.5 Block 6.

Reconstruction and promotion are deliberately separate operations. A retrieved
CONTCAR may always be reconstructed as an immutable candidate when provenance is
valid, but only a scientifically converged candidate may explicitly replace a
StructureVariant's current snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import UUID

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    CalculationType,
    Lattice,
    MethodFingerprint,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
)
from ecatvasp.domain.ids import (
    ArtifactId,
    AtomUid,
    CalculationId,
    ExecutionAttemptId,
    StructureSnapshotId,
)
from ecatvasp.provenance import scientific_hash
from ecatvasp.structures.identity import validate_identity_preserving_revision
from ecatvasp.vasp.convergence import VaspConvergenceEvidence, assess_vasp_convergence
from ecatvasp.vasp.execution_plan import ExecutionPlan, StagingInput, StagingInputKind
from ecatvasp.vasp.result_intake import VaspResultArtifactIntake, VaspResultInputFile
from ecatvasp.vasp.results import (
    ConvergenceVerdict,
    VaspConvergenceAssessment,
    VaspResultSourceRole,
)

VASP_CONTCAR_RECONSTRUCTOR_NAME = "ecatvasp.vasp.contcar-reconstructor"
VASP_CONTCAR_RECONSTRUCTOR_VERSION = "1"

_RELAX_TYPES = frozenset({CalculationType.RELAX, CalculationType.GAS_RELAX})


class VaspStructurePromotionError(ValueError):
    """Raised when CONTCAR reconstruction or promotion would require guessing."""


@dataclass(frozen=True, slots=True)
class VaspContcarReconstruction:
    """One immutable CONTCAR-derived candidate bound to exact execution provenance."""

    calculation_id: CalculationId
    intake_hash: str
    attempt_id: ExecutionAttemptId
    source_artifact_id: ArtifactId
    source_sha256: str
    input_snapshot_id: StructureSnapshotId
    snapshot: StructureSnapshot

    def __post_init__(self) -> None:
        if self.snapshot.parent_snapshot_id != self.input_snapshot_id:
            raise VaspStructurePromotionError(
                "reconstructed snapshot must directly reference the input snapshot"
            )
        if self.snapshot.origin is not StructureOrigin.RELAXED:
            raise VaspStructurePromotionError(
                "reconstructed CONTCAR snapshot must have RELAXED origin"
            )
        _validate_sha256(self.intake_hash, "intake_hash")
        _validate_sha256(self.source_sha256, "source_sha256")


@dataclass(frozen=True, slots=True)
class VaspStructurePromotionResult:
    """Pure explicit promotion result; persistence remains a caller responsibility."""

    updated_variant: StructureVariant
    snapshot: StructureSnapshot
    convergence: VaspConvergenceAssessment


@dataclass(frozen=True, slots=True)
class _AtomMapEntry:
    atom_uid: AtomUid
    element: str
    snapshot_index: int
    poscar_index: int
    vasp_ordinal: int


@dataclass(frozen=True, slots=True)
class _ParsedVaspStructure:
    lattice: Lattice
    elements: tuple[str, ...]
    fractional_coords: tuple[tuple[float, float, float], ...]


def reconstruct_vasp_contcar_snapshot(
    *,
    project_root: Path | str,
    calculation: Calculation,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    input_snapshot: StructureSnapshot,
    label: str | None = None,
) -> VaspContcarReconstruction:
    """Reconstruct one immutable candidate snapshot from the exact retrieved CONTCAR."""

    _validate_reconstruction_identity(
        calculation=calculation,
        plan=plan,
        intake=intake,
        input_snapshot=input_snapshot,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise VaspStructurePromotionError("project_root must be an existing directory")

    atom_map_input = _require_staging_input(plan, "atom_index_map")
    poscar_input = _require_staging_input(plan, "poscar")
    _validate_staging_role(
        item=atom_map_input,
        artifact_type=ArtifactType.DERIVED_DATASET,
        kind=StagingInputKind.METADATA,
        target="atom-index-map.json",
    )
    _validate_staging_role(
        item=poscar_input,
        artifact_type=ArtifactType.POSCAR,
        kind=StagingInputKind.VASP_INPUT,
        target="POSCAR",
    )
    atom_map_body = _read_staging_bytes(root=root, item=atom_map_input)
    poscar_body = _read_staging_bytes(root=root, item=poscar_input)
    atom_map = _parse_atom_map(
        body=atom_map_body,
        input_snapshot=input_snapshot,
        poscar_sha256=poscar_input.sha256,
    )
    staged_poscar = _parse_vasp_structure_bytes(poscar_body, source_name="staged POSCAR")
    expected_elements = tuple(entry.element for entry in atom_map)
    if staged_poscar.elements != expected_elements:
        raise VaspStructurePromotionError(
            "atom index map element order does not match the exact staged POSCAR"
        )

    contcar_input = _require_result_file(intake, VaspResultSourceRole.CONTCAR)
    contcar_body = _read_result_bytes(root=root, item=contcar_input)
    contcar = _parse_vasp_structure_bytes(contcar_body, source_name="CONTCAR")
    if contcar.elements != expected_elements:
        raise VaspStructurePromotionError(
            "CONTCAR atom count/species/order does not match the exact atom index map"
        )

    sites = tuple(
        StructureSite(
            atom_uid=entry.atom_uid,
            element=entry.element,
            fractional_coords=coords,
        )
        for entry, coords in zip(atom_map, contcar.fractional_coords, strict=True)
    )
    candidate = StructureSnapshot(
        lattice=contcar.lattice,
        sites=sites,
        label=label or _default_candidate_label(input_snapshot, calculation),
        origin=StructureOrigin.RELAXED,
        parent_snapshot_id=input_snapshot.id,
        periodic=input_snapshot.periodic,
    )
    validate_identity_preserving_revision(source=input_snapshot, target=candidate)
    return VaspContcarReconstruction(
        calculation_id=calculation.id,
        intake_hash=intake.intake_hash,
        attempt_id=intake.attempt_id,
        source_artifact_id=contcar_input.source.artifact_id,
        source_sha256=contcar_input.source.sha256,
        input_snapshot_id=input_snapshot.id,
        snapshot=candidate,
    )


def promote_vasp_contcar_snapshot(
    *,
    variant: StructureVariant,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    evidence: VaspConvergenceEvidence,
    input_snapshot: StructureSnapshot,
    reconstruction: VaspContcarReconstruction,
) -> VaspStructurePromotionResult:
    """Explicitly promote a converged CONTCAR candidate to a StructureVariant current snapshot."""

    if reconstruction.calculation_id != calculation.id:
        raise VaspStructurePromotionError("reconstruction belongs to another Calculation")
    if reconstruction.input_snapshot_id != input_snapshot.id:
        raise VaspStructurePromotionError("reconstruction belongs to another input snapshot")
    if calculation.input_structure_snapshot_id != input_snapshot.id:
        raise VaspStructurePromotionError(
            "Calculation does not reference the supplied input snapshot"
        )
    if evidence.calculation_id != calculation.id:
        raise VaspStructurePromotionError("convergence evidence belongs to another Calculation")
    if evidence.intake_hash.lower() != reconstruction.intake_hash.lower():
        raise VaspStructurePromotionError(
            "convergence evidence and CONTCAR reconstruction use different result intakes"
        )
    validate_identity_preserving_revision(
        source=input_snapshot,
        target=reconstruction.snapshot,
    )
    if variant.current_structure_snapshot_id != input_snapshot.id:
        raise VaspStructurePromotionError(
            "StructureVariant current snapshot has moved since this Calculation started"
        )

    assessment = assess_vasp_convergence(
        calculation=calculation,
        fingerprint=fingerprint,
        evidence=evidence,
    )
    if assessment.overall is not ConvergenceVerdict.CONVERGED:
        raise VaspStructurePromotionError(
            "only scientifically converged CONTCAR candidates may be promoted"
        )
    updated_variant = replace(
        variant,
        current_structure_snapshot_id=reconstruction.snapshot.id,
    )
    return VaspStructurePromotionResult(
        updated_variant=updated_variant,
        snapshot=reconstruction.snapshot,
        convergence=assessment,
    )


def _validate_reconstruction_identity(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    input_snapshot: StructureSnapshot,
) -> None:
    if calculation.calculation_type not in _RELAX_TYPES:
        raise VaspStructurePromotionError("CONTCAR reconstruction requires a relax Calculation")
    if calculation.input_structure_snapshot_id != input_snapshot.id:
        raise VaspStructurePromotionError(
            "Calculation does not reference the supplied input snapshot"
        )
    if plan.calculation_id != calculation.id or intake.calculation_id != calculation.id:
        raise VaspStructurePromotionError("plan/intake belongs to another Calculation")
    if plan.recipe_id != calculation.recipe_id or intake.recipe_id != calculation.recipe_id:
        raise VaspStructurePromotionError("plan/intake recipe does not match Calculation")
    if intake.calculation_type is not calculation.calculation_type:
        raise VaspStructurePromotionError(
            "result intake CalculationType does not match Calculation"
        )
    if intake.plan_hash != plan.plan_hash:
        raise VaspStructurePromotionError(
            "result intake does not reference the exact ExecutionPlan"
        )
    if intake.input_manifest_hash != plan.input_manifest_sha256:
        raise VaspStructurePromotionError(
            "result intake input manifest does not match ExecutionPlan"
        )


def _parse_atom_map(
    *,
    body: bytes,
    input_snapshot: StructureSnapshot,
    poscar_sha256: str,
) -> tuple[_AtomMapEntry, ...]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VaspStructurePromotionError("atom-index-map.json is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise VaspStructurePromotionError("atom-index-map.json root must be an object")
    if payload.get("format") != "ecatvasp-v03-atom-index-map" or payload.get("version") != 1:
        raise VaspStructurePromotionError("unsupported atom-index-map.json format/version")
    if payload.get("structure_snapshot_id") != str(input_snapshot.id):
        raise VaspStructurePromotionError("atom index map belongs to another input snapshot")
    if payload.get("structure_sha256") != scientific_hash(input_snapshot):
        raise VaspStructurePromotionError(
            "atom index map does not bind the exact input snapshot content"
        )
    if payload.get("poscar_sha256") != poscar_sha256:
        raise VaspStructurePromotionError("atom index map does not bind the exact staged POSCAR")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise VaspStructurePromotionError("atom index map requires non-empty entries")
    if len(raw_entries) != len(input_snapshot.sites):
        raise VaspStructurePromotionError("atom index map atom count does not match input snapshot")

    entries: list[_AtomMapEntry] = []
    snapshot_indices: list[int] = []
    for poscar_index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise VaspStructurePromotionError("atom index map entry must be an object")
        snapshot_index = raw.get("snapshot_index")
        raw_poscar_index = raw.get("poscar_index")
        vasp_ordinal = raw.get("vasp_ordinal")
        if (
            isinstance(snapshot_index, bool)
            or not isinstance(snapshot_index, int)
            or snapshot_index < 0
            or snapshot_index >= len(input_snapshot.sites)
        ):
            raise VaspStructurePromotionError("atom index map snapshot_index is invalid")
        if raw_poscar_index != poscar_index or vasp_ordinal != poscar_index + 1:
            raise VaspStructurePromotionError("atom index map POSCAR/VASP order is not contiguous")
        raw_uid = raw.get("atom_uid")
        element = raw.get("element")
        if not isinstance(raw_uid, str) or not isinstance(element, str) or not element.strip():
            raise VaspStructurePromotionError("atom index map atom identity fields are invalid")
        try:
            atom_uid = AtomUid(UUID(raw_uid))
        except ValueError as error:
            raise VaspStructurePromotionError("atom index map atom_uid is not a UUID") from error
        source_site = input_snapshot.sites[snapshot_index]
        if atom_uid != source_site.atom_uid or element != source_site.element:
            raise VaspStructurePromotionError(
                "atom index map identity does not match the immutable input snapshot"
            )
        snapshot_indices.append(snapshot_index)
        entries.append(
            _AtomMapEntry(
                atom_uid=atom_uid,
                element=element,
                snapshot_index=snapshot_index,
                poscar_index=poscar_index,
                vasp_ordinal=poscar_index + 1,
            )
        )
    if set(snapshot_indices) != set(range(len(input_snapshot.sites))):
        raise VaspStructurePromotionError("atom index map snapshot indices must be a permutation")
    if len({entry.atom_uid for entry in entries}) != len(entries):
        raise VaspStructurePromotionError("atom index map atom_uids must be unique")
    return tuple(entries)


def _parse_vasp_structure_bytes(body: bytes, *, source_name: str) -> _ParsedVaspStructure:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise VaspStructurePromotionError(f"{source_name} is not valid UTF-8") from error
    lines = [line.rstrip() for line in text.splitlines()]
    if len(lines) < 8:
        raise VaspStructurePromotionError(f"{source_name} is too short")
    scale_tokens = lines[1].split()
    if len(scale_tokens) != 1:
        raise VaspStructurePromotionError(f"{source_name} requires one universal scale factor")
    scale = _parse_float(scale_tokens[0], f"{source_name} scale factor")
    if scale <= 0:
        raise VaspStructurePromotionError(
            f"{source_name} requires a positive scale factor; "
            "negative volume scaling is unsupported"
        )

    lattice = Lattice(
        vectors=(
            _scaled_vector(lines[2], scale, source_name),
            _scaled_vector(lines[3], scale, source_name),
            _scaled_vector(lines[4], scale, source_name),
        )
    )
    symbols = lines[5].split()
    if not symbols or all(_looks_numeric(token) for token in symbols):
        raise VaspStructurePromotionError(f"{source_name} requires VASP5-style element symbols")
    try:
        counts = tuple(int(token) for token in lines[6].split())
    except ValueError as error:
        raise VaspStructurePromotionError(f"{source_name} has invalid element counts") from error
    if len(counts) != len(symbols) or any(count < 1 for count in counts):
        raise VaspStructurePromotionError(f"{source_name} element symbols/counts are inconsistent")
    elements = tuple(
        symbol
        for symbol, count in zip(symbols, counts, strict=True)
        for _ in range(count)
    )

    cursor = 7
    if lines[cursor].strip().lower().startswith("s"):
        cursor += 1
    if cursor >= len(lines):
        raise VaspStructurePromotionError(f"{source_name} is missing coordinate mode")
    mode = lines[cursor].strip().lower()
    cursor += 1
    if len(lines) < cursor + len(elements):
        raise VaspStructurePromotionError(f"{source_name} has fewer coordinate rows than atoms")
    coordinates = tuple(
        _parse_coordinate_row(lines[cursor + index], source_name)
        for index in range(len(elements))
    )
    if mode.startswith("d"):
        fractional = coordinates
    elif mode.startswith("c") or mode.startswith("k"):
        scaled_cartesian = tuple(
            (coord[0] * scale, coord[1] * scale, coord[2] * scale)
            for coord in coordinates
        )
        fractional = tuple(
            _cartesian_to_fractional(coord, lattice, source_name)
            for coord in scaled_cartesian
        )
    else:
        raise VaspStructurePromotionError(f"unsupported {source_name} coordinate mode")
    return _ParsedVaspStructure(
        lattice=lattice,
        elements=elements,
        fractional_coords=fractional,
    )


def _scaled_vector(
    line: str,
    scale: float,
    source_name: str,
) -> tuple[float, float, float]:
    values = _parse_coordinate_row(line, source_name)
    return values[0] * scale, values[1] * scale, values[2] * scale


def _parse_coordinate_row(line: str, source_name: str) -> tuple[float, float, float]:
    tokens = line.split()
    if len(tokens) < 3:
        raise VaspStructurePromotionError(f"{source_name} coordinate row requires three values")
    values = tuple(_parse_float(token, f"{source_name} coordinate") for token in tokens[:3])
    return values[0], values[1], values[2]


def _parse_float(value: str, field_name: str) -> float:
    try:
        result = float(value.replace("D", "E").replace("d", "e"))
    except ValueError as error:
        raise VaspStructurePromotionError(f"invalid numeric {field_name}") from error
    if not math.isfinite(result):
        raise VaspStructurePromotionError(f"{field_name} must be finite")
    return result


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _cartesian_to_fractional(
    cartesian: tuple[float, float, float],
    lattice: Lattice,
    source_name: str,
) -> tuple[float, float, float]:
    a, b, c = lattice.vectors
    det = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if abs(det) < 1e-14:
        raise VaspStructurePromotionError(f"{source_name} lattice is singular")
    x, y, z = cartesian
    f1 = (
        x * (b[1] * c[2] - b[2] * c[1])
        - y * (b[0] * c[2] - b[2] * c[0])
        + z * (b[0] * c[1] - b[1] * c[0])
    ) / det
    f2 = (
        -x * (a[1] * c[2] - a[2] * c[1])
        + y * (a[0] * c[2] - a[2] * c[0])
        - z * (a[0] * c[1] - a[1] * c[0])
    ) / det
    f3 = (
        x * (a[1] * b[2] - a[2] * b[1])
        - y * (a[0] * b[2] - a[2] * b[0])
        + z * (a[0] * b[1] - a[1] * b[0])
    ) / det
    return f1, f2, f3


def _require_staging_input(plan: ExecutionPlan, role: str) -> StagingInput:
    matches = tuple(item for item in plan.staging_inputs if item.role == role)
    if len(matches) != 1:
        raise VaspStructurePromotionError(f"ExecutionPlan requires exactly one {role} input")
    return matches[0]


def _validate_staging_role(
    *,
    item: StagingInput,
    artifact_type: ArtifactType,
    kind: StagingInputKind,
    target: str,
) -> None:
    if item.artifact_type is not artifact_type or item.kind is not kind:
        raise VaspStructurePromotionError(f"staging role {item.role!r} has incompatible metadata")
    if item.target_relative_path != target:
        raise VaspStructurePromotionError(f"staging role {item.role!r} has unexpected target path")


def _require_result_file(
    intake: VaspResultArtifactIntake,
    role: VaspResultSourceRole,
) -> VaspResultInputFile:
    matches = tuple(item for item in intake.files if item.source.role is role)
    if len(matches) != 1:
        raise VaspStructurePromotionError(f"result intake requires exactly one {role.value} source")
    return matches[0]


def _read_staging_bytes(*, root: Path, item: StagingInput) -> bytes:
    path = _resolve_relative_path(root, item.source_relative_path, "staging input")
    body = path.read_bytes()
    _validate_bytes(body, item.size_bytes, item.sha256, f"staging role {item.role!r}")
    return body


def _read_result_bytes(*, root: Path, item: VaspResultInputFile) -> bytes:
    path = _resolve_relative_path(root, item.local_relative_path, "result source")
    body = path.read_bytes()
    _validate_bytes(body, item.size_bytes, item.source.sha256, item.source.role.value)
    return body


def _validate_bytes(body: bytes, size_bytes: int, sha256: str, label: str) -> None:
    if len(body) != size_bytes:
        raise VaspStructurePromotionError(f"{label} size changed after intake")
    if hashlib.sha256(body).hexdigest() != sha256.lower():
        raise VaspStructurePromotionError(f"{label} SHA-256 changed after intake")


def _resolve_relative_path(root: Path, value: str, field_name: str) -> Path:
    relative = PurePosixPath(value)
    invalid = (
        relative.is_absolute()
        or value != relative.as_posix()
        or ".." in relative.parts
        or value in {"", "."}
    )
    if invalid:
        raise VaspStructurePromotionError(
            f"{field_name} path must be a normalized relative POSIX path"
        )
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise VaspStructurePromotionError(f"{field_name} resolves outside project_root")
    if not path.is_file():
        raise VaspStructurePromotionError(f"{field_name} file is missing")
    return path


def _validate_sha256(value: str, field_name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise VaspStructurePromotionError(f"{field_name} must be a SHA-256 digest")


def _default_candidate_label(
    input_snapshot: StructureSnapshot,
    calculation: Calculation,
) -> str:
    base = input_snapshot.label or calculation.recipe_id
    return f"{base} relaxed CONTCAR"
