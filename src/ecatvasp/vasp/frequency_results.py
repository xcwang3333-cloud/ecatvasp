"""Frequency-result enrichment for v0.5 Block 7.

The parser consumes one exact managed VASP result intake plus the immutable input
identity needed to map VASP-local mode vectors back to permanent ``atom_uid``
values. It does not classify thermodynamic stability, calculate ZPE/entropy, or
mutate Calculation/ExecutionAttempt lifecycle state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import UUID

from ecatvasp.domain import ArtifactType, Calculation, CalculationType, MethodFingerprint
from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.ids import AtomUid
from ecatvasp.provenance import scientific_hash
from ecatvasp.vasp.execution_plan import ExecutionPlan, StagingInput, StagingInputKind
from ecatvasp.vasp.frequency import (
    FrequencySelection,
    validate_frequency_fingerprint_selection,
    validate_frequency_recipe,
)
from ecatvasp.vasp.recipes import (
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    get_vasp_recipe_spec,
)
from ecatvasp.vasp.result_intake import VaspResultArtifactIntake, VaspResultInputFile
from ecatvasp.vasp.results import (
    VaspFrequencyDataset,
    VaspFrequencyEigenvector,
    VaspFrequencyMode,
    VaspFrequencyModeKind,
    VaspResultDocument,
    VaspResultSourceRole,
)

VASP_FREQUENCY_RESULT_PARSER_NAME = "ecatvasp.vasp.frequency-result-parser"
VASP_FREQUENCY_RESULT_PARSER_VERSION = "1"

_FREQUENCY_TYPES = frozenset({CalculationType.FREQUENCY, CalculationType.GAS_FREQUENCY})
_FREQUENCY_RECIPES = frozenset(
    {RECIPE_SELECTED_ATOM_FREQUENCY, RECIPE_FULL_FREQUENCY, RECIPE_GAS_FREQUENCY}
)
_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_MODE_LINE = re.compile(
    rf"^\s*(\d+)\s+f(?P<imag>/i)?\s*=\s*({_FLOAT})\s+THz\s+"
    rf"({_FLOAT})\s+2PiTHz\s+({_FLOAT})\s+cm-1\s+({_FLOAT})\s+meV\s*$",
    re.IGNORECASE,
)
_DYNAMICAL_HEADER = "Eigenvectors and eigenvalues of the dynamical matrix"
_SQRT_MASS_HEADER = "Eigenvectors after division by SQRT(mass)"


class VaspFrequencyResultError(ValueError):
    """Raised when VASP frequency results cannot be interpreted without guessing."""


@dataclass(frozen=True, slots=True)
class _AtomMapEntry:
    atom_uid: AtomUid
    element: str
    snapshot_index: int
    poscar_index: int
    vasp_ordinal: int
    selective_dynamics: tuple[bool, bool, bool] | None


def parse_vasp_frequency_results(
    *,
    project_root: Path | str,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    input_snapshot: StructureSnapshot,
    result: VaspResultDocument,
) -> VaspResultDocument:
    """Enrich one normalized VASP result with exact finite-difference modes."""

    _validate_identity(
        calculation=calculation,
        fingerprint=fingerprint,
        plan=plan,
        intake=intake,
        input_snapshot=input_snapshot,
        result=result,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise VaspFrequencyResultError("project_root must be an existing directory")

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
    _read_staging_bytes(root=root, item=poscar_input)
    atom_map = _parse_atom_map(
        body=atom_map_body,
        input_snapshot=input_snapshot,
        poscar_sha256=poscar_input.sha256,
    )
    displaced_uids = _resolve_displaced_uids(
        fingerprint=fingerprint,
        atom_map=atom_map,
    )

    outcar_input = _require_result_file(intake, VaspResultSourceRole.OUTCAR)
    outcar_body = _read_result_bytes(root=root, item=outcar_input)
    try:
        outcar_text = outcar_body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise VaspFrequencyResultError("OUTCAR is not valid UTF-8 text") from error

    atom_uids = tuple(entry.atom_uid for entry in atom_map)
    modes = _parse_canonical_modes(outcar_text, atom_uids=atom_uids)
    dataset = VaspFrequencyDataset(
        atom_uids=atom_uids,
        displaced_atom_uids=displaced_uids,
        modes=modes,
    )
    codes = set(result.evidence_codes)
    codes.add("frequency.atom_index_map_verified")
    codes.add("frequency.standard_dynamical_matrix_block")
    codes.add("frequency.mode_count_verified")
    if dataset.imaginary_mode_count:
        codes.add("frequency.imaginary_modes_observed")
    return replace(
        result,
        frequencies=dataset,
        evidence_codes=tuple(sorted(codes)),
    )


def _validate_identity(
    *,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    input_snapshot: StructureSnapshot,
    result: VaspResultDocument,
) -> None:
    if calculation.calculation_type not in _FREQUENCY_TYPES:
        raise VaspFrequencyResultError("frequency result parsing requires a frequency Calculation")
    if calculation.recipe_id not in _FREQUENCY_RECIPES:
        raise VaspFrequencyResultError("Calculation recipe is not a frequency recipe")
    if calculation.input_structure_snapshot_id != input_snapshot.id:
        raise VaspFrequencyResultError("Calculation does not reference the supplied input snapshot")
    if calculation.method_fingerprint_id != fingerprint.id:
        raise VaspFrequencyResultError("Calculation does not reference the supplied MethodFingerprint")
    if calculation.recipe_id != fingerprint.recipe.recipe_id:
        raise VaspFrequencyResultError("Calculation recipe does not match MethodFingerprint recipe")
    spec = get_vasp_recipe_spec(calculation.recipe_id)
    if spec.calculation_type is not calculation.calculation_type:
        raise VaspFrequencyResultError("CalculationType does not match canonical frequency recipe")
    if fingerprint.recipe.version != spec.version:
        raise VaspFrequencyResultError("MethodFingerprint frequency recipe version is not canonical")
    validate_frequency_recipe(fingerprint.recipe)
    if plan.calculation_id != calculation.id or intake.calculation_id != calculation.id:
        raise VaspFrequencyResultError("plan/intake belongs to another Calculation")
    if plan.recipe_id != calculation.recipe_id or intake.recipe_id != calculation.recipe_id:
        raise VaspFrequencyResultError("plan/intake recipe does not match Calculation")
    if intake.calculation_type is not calculation.calculation_type:
        raise VaspFrequencyResultError("result intake CalculationType does not match Calculation")
    if intake.plan_hash != plan.plan_hash:
        raise VaspFrequencyResultError("result intake does not reference the exact ExecutionPlan")
    if intake.input_manifest_hash != plan.input_manifest_sha256:
        raise VaspFrequencyResultError("result intake input manifest does not match ExecutionPlan")
    if result.calculation_type is not calculation.calculation_type:
        raise VaspFrequencyResultError("normalized result CalculationType does not match Calculation")
    if result.sources != intake.sources:
        raise VaspFrequencyResultError("normalized result sources do not match exact result intake")
    if result.frequencies is not None:
        raise VaspFrequencyResultError("normalized result already contains frequency data")


def _resolve_displaced_uids(
    *,
    fingerprint: MethodFingerprint,
    atom_map: tuple[_AtomMapEntry, ...],
) -> tuple[AtomUid, ...]:
    recipe_id = fingerprint.recipe.recipe_id
    if recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY:
        selected: list[AtomUid] = []
        for entry in atom_map:
            flags = entry.selective_dynamics
            if flags == (True, True, True):
                selected.append(entry.atom_uid)
            elif flags != (False, False, False):
                raise VaspFrequencyResultError(
                    "SelectedAtomFrequency atom map requires only T T T / F F F flags"
                )
        if not selected:
            raise VaspFrequencyResultError("SelectedAtomFrequency atom map selects no atoms")
        selection = FrequencySelection(tuple(selected))
        try:
            validate_frequency_fingerprint_selection(
                fingerprint=fingerprint,
                selection=selection,
            )
        except ValueError as error:
            raise VaspFrequencyResultError(str(error)) from error
        return tuple(selected)

    if recipe_id in {RECIPE_FULL_FREQUENCY, RECIPE_GAS_FREQUENCY}:
        if any(entry.selective_dynamics is not None for entry in atom_map):
            raise VaspFrequencyResultError(
                "full/gas frequency atom map must not contain Selective Dynamics flags"
            )
        try:
            validate_frequency_fingerprint_selection(
                fingerprint=fingerprint,
                selection=None,
            )
        except ValueError as error:
            raise VaspFrequencyResultError(str(error)) from error
        return tuple(entry.atom_uid for entry in atom_map)

    raise VaspFrequencyResultError("unsupported frequency recipe")


def _parse_canonical_modes(
    text: str,
    *,
    atom_uids: tuple[AtomUid, ...],
) -> tuple[VaspFrequencyMode, ...]:
    lines = text.splitlines()
    headers: list[int] = []
    for index, line in enumerate(lines):
        if line.strip() != _DYNAMICAL_HEADER:
            continue
        if _is_sqrt_mass_block(lines, index):
            continue
        headers.append(index)
    if not headers:
        raise VaspFrequencyResultError("OUTCAR is missing the standard dynamical-matrix mode block")
    if len(headers) != 1:
        raise VaspFrequencyResultError("OUTCAR contains multiple canonical dynamical-matrix blocks")

    start = headers[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].strip() == _DYNAMICAL_HEADER:
            end = index
            break

    modes: list[VaspFrequencyMode] = []
    cursor = start
    while cursor < end:
        match = _MODE_LINE.match(lines[cursor])
        if match is None:
            cursor += 1
            continue
        mode_index = int(match.group(1))
        kind = (
            VaspFrequencyModeKind.IMAGINARY
            if match.group("imag") is not None
            else VaspFrequencyModeKind.REAL
        )
        values = tuple(_parse_float(match.group(i)) for i in range(3, 7))
        cursor += 1
        while cursor < end and not lines[cursor].strip():
            cursor += 1
        if cursor >= end or tuple(lines[cursor].split())[:6] != ("X", "Y", "Z", "dx", "dy", "dz"):
            raise VaspFrequencyResultError(
                f"frequency mode {mode_index} is missing its eigenvector table header"
            )
        cursor += 1
        vectors: list[VaspFrequencyEigenvector] = []
        for atom_uid in atom_uids:
            if cursor >= end:
                raise VaspFrequencyResultError(
                    f"frequency mode {mode_index} has an incomplete eigenvector table"
                )
            tokens = lines[cursor].split()
            if len(tokens) < 6:
                raise VaspFrequencyResultError(
                    f"frequency mode {mode_index} has an incomplete eigenvector row"
                )
            row = tuple(_parse_float(token) for token in tokens[:6])
            vectors.append(
                VaspFrequencyEigenvector(
                    atom_uid=atom_uid,
                    components=(row[3], row[4], row[5]),
                )
            )
            cursor += 1
        modes.append(
            VaspFrequencyMode(
                mode_index=mode_index,
                kind=kind,
                frequency_thz=values[0],
                angular_frequency_2pi_thz=values[1],
                wavenumber_cm_inverse=values[2],
                energy_mev=values[3],
                eigenvectors=tuple(vectors),
            )
        )

    if not modes:
        raise VaspFrequencyResultError("OUTCAR dynamical-matrix block contains no modes")
    indices = tuple(mode.mode_index for mode in modes)
    if indices != tuple(range(1, len(modes) + 1)):
        raise VaspFrequencyResultError("frequency mode indices must be contiguous from 1")
    return tuple(modes)


def _is_sqrt_mass_block(lines: list[str], header_index: int) -> bool:
    inspected = 0
    cursor = header_index - 1
    while cursor >= 0 and inspected < 4:
        stripped = lines[cursor].strip()
        cursor -= 1
        if not stripped:
            continue
        inspected += 1
        if stripped == _SQRT_MASS_HEADER:
            return True
        if stripped.startswith("-"):
            continue
        break
    return False


def _parse_atom_map(
    *,
    body: bytes,
    input_snapshot: StructureSnapshot,
    poscar_sha256: str,
) -> tuple[_AtomMapEntry, ...]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VaspFrequencyResultError("atom-index-map.json is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise VaspFrequencyResultError("atom-index-map.json root must be an object")
    if payload.get("format") != "ecatvasp-v03-atom-index-map" or payload.get("version") != 1:
        raise VaspFrequencyResultError("unsupported atom-index-map.json format/version")
    if payload.get("structure_snapshot_id") != str(input_snapshot.id):
        raise VaspFrequencyResultError("atom index map belongs to another input snapshot")
    if payload.get("structure_sha256") != scientific_hash(input_snapshot):
        raise VaspFrequencyResultError("atom index map does not bind the exact input structure hash")
    if payload.get("poscar_sha256") != poscar_sha256:
        raise VaspFrequencyResultError("atom index map does not bind the exact staged POSCAR")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != len(input_snapshot.sites):
        raise VaspFrequencyResultError("atom index map atom count does not match input snapshot")

    entries: list[_AtomMapEntry] = []
    snapshot_indices: list[int] = []
    for poscar_index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise VaspFrequencyResultError("atom index map entry must be an object")
        snapshot_index = raw.get("snapshot_index")
        raw_poscar_index = raw.get("poscar_index")
        vasp_ordinal = raw.get("vasp_ordinal")
        if (
            isinstance(snapshot_index, bool)
            or not isinstance(snapshot_index, int)
            or snapshot_index < 0
            or snapshot_index >= len(input_snapshot.sites)
        ):
            raise VaspFrequencyResultError("atom index map snapshot_index is invalid")
        if raw_poscar_index != poscar_index or vasp_ordinal != poscar_index + 1:
            raise VaspFrequencyResultError("atom index map POSCAR/VASP order is not contiguous")
        raw_uid = raw.get("atom_uid")
        element = raw.get("element")
        if not isinstance(raw_uid, str) or not isinstance(element, str) or not element.strip():
            raise VaspFrequencyResultError("atom index map atom identity fields are invalid")
        try:
            atom_uid = AtomUid(UUID(raw_uid))
        except ValueError as error:
            raise VaspFrequencyResultError("atom index map atom_uid is not a UUID") from error
        source_site = input_snapshot.sites[snapshot_index]
        if atom_uid != source_site.atom_uid or element != source_site.element:
            raise VaspFrequencyResultError(
                "atom index map identity does not match the immutable input snapshot"
            )
        flags = _parse_selective_flags(raw.get("selective_dynamics"))
        snapshot_indices.append(snapshot_index)
        entries.append(
            _AtomMapEntry(
                atom_uid=atom_uid,
                element=element,
                snapshot_index=snapshot_index,
                poscar_index=poscar_index,
                vasp_ordinal=poscar_index + 1,
                selective_dynamics=flags,
            )
        )
    if set(snapshot_indices) != set(range(len(input_snapshot.sites))):
        raise VaspFrequencyResultError("atom index map snapshot indices must be a permutation")
    if len({entry.atom_uid for entry in entries}) != len(entries):
        raise VaspFrequencyResultError("atom index map atom_uids must be unique")
    return tuple(entries)


def _parse_selective_flags(value: object) -> tuple[bool, bool, bool] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3 or any(type(item) is not bool for item in value):
        raise VaspFrequencyResultError("atom index map selective_dynamics flags are invalid")
    return (value[0], value[1], value[2])


def _require_staging_input(plan: ExecutionPlan, role: str) -> StagingInput:
    matches = tuple(item for item in plan.staging_inputs if item.role == role)
    if len(matches) != 1:
        raise VaspFrequencyResultError(f"ExecutionPlan requires exactly one {role} staging input")
    return matches[0]


def _validate_staging_role(
    *,
    item: StagingInput,
    artifact_type: ArtifactType,
    kind: StagingInputKind,
    target: str,
) -> None:
    if item.artifact_type is not artifact_type or item.kind is not kind:
        raise VaspFrequencyResultError(f"staging input role {item.role!r} has the wrong contract")
    if item.target_relative_path != target:
        raise VaspFrequencyResultError(f"staging input role {item.role!r} targets the wrong path")


def _require_result_file(
    intake: VaspResultArtifactIntake,
    role: VaspResultSourceRole,
) -> VaspResultInputFile:
    matches = tuple(item for item in intake.files if item.source.role is role)
    if len(matches) != 1:
        raise VaspFrequencyResultError(f"result intake requires exactly one {role.value}")
    return matches[0]


def _read_staging_bytes(*, root: Path, item: StagingInput) -> bytes:
    path = _resolve_relative_path(root=root, relative=item.source_relative_path)
    body = path.read_bytes()
    _validate_bytes(body=body, expected_size=item.size_bytes, expected_sha256=item.sha256)
    return body


def _read_result_bytes(*, root: Path, item: VaspResultInputFile) -> bytes:
    path = _resolve_relative_path(root=root, relative=item.local_relative_path)
    body = path.read_bytes()
    _validate_bytes(
        body=body,
        expected_size=item.size_bytes,
        expected_sha256=item.source.sha256,
    )
    return body


def _resolve_relative_path(*, root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts or relative != posix.as_posix():
        raise VaspFrequencyResultError("managed file path must be a normalized relative POSIX path")
    path = (root / Path(*posix.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise VaspFrequencyResultError("managed file path escapes project_root") from error
    if not path.is_file():
        raise VaspFrequencyResultError("managed file is missing at frequency-parse time")
    return path


def _validate_bytes(*, body: bytes, expected_size: int, expected_sha256: str) -> None:
    if len(body) != expected_size:
        raise VaspFrequencyResultError("managed file size changed after result intake")
    digest = hashlib.sha256(body).hexdigest()
    if digest != expected_sha256.lower():
        raise VaspFrequencyResultError("managed file SHA-256 changed after result intake")


def _parse_float(token: str) -> float:
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError as error:
        raise VaspFrequencyResultError(f"invalid numeric value in frequency block: {token}") from error
    if not (-float("inf") < value < float("inf")):
        raise VaspFrequencyResultError("frequency block contains a non-finite numeric value")
    return value
