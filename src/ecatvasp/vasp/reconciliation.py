"""Manifest-aware reconciliation for immutable ECatVASP-generated VASP inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from ecatvasp.domain import Calculation, MethodFingerprint, StructureSnapshot, scientific_hash
from ecatvasp.domain.ids import AtomUid
from ecatvasp.domain.method import ProtocolDefinition
from ecatvasp.vasp.analysis_prerequisites import prepare_analysis_prerequisite_incar
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.frequency import (
    prepare_frequency_incar,
    validate_frequency_prepared_poscar,
)
from ecatvasp.vasp.incar import PreparedIncar, prepare_incar
from ecatvasp.vasp.kpoints import (
    ECATVASP_KPOINT_CENTERING,
    KPointCentering,
    PreparedKPoints,
    prepare_kpoints,
    validate_protocol_kpoint_contract,
)
from ecatvasp.vasp.materialization import (
    INPUT_GENERATOR_NAME,
    INPUT_GENERATOR_VERSION,
    INPUT_MANIFEST_FORMAT,
    INPUT_MANIFEST_VERSION,
    ManifestFileRecord,
)
from ecatvasp.vasp.poscar import (
    AtomSelectiveFlags,
    PreparedPoscar,
    UidSelectiveDynamics,
    prepare_poscar,
)
from ecatvasp.vasp.potcar import PotcarSpec, PotcarSpecEntry
from ecatvasp.vasp.preflight import VaspFailClosedCode, VaspPreflightError, fail_closed
from ecatvasp.vasp.recipes import (
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    validate_calculation_recipe_contract,
)

_FREQUENCY_RECIPES = frozenset(
    {
        RECIPE_SELECTED_ATOM_FREQUENCY,
        RECIPE_FULL_FREQUENCY,
        RECIPE_GAS_FREQUENCY,
    }
)
_ANALYSIS_RECIPES = frozenset(
    {
        RECIPE_DOS_PREREQUISITE,
        RECIPE_CHARGE_DENSITY_STATIC,
        RECIPE_LOBSTER_PREREQUISITE,
    }
)
_EXPECTED_ROLE_FILENAMES = {
    "incar": "INCAR",
    "poscar": "POSCAR",
    "potcar_spec": "POTCAR.spec",
    "atom_index_map": "atom-index-map.json",
    "kpoints": "KPOINTS",
}
_ATOM_INDEX_MAP_FORMAT = "ecatvasp-v03-atom-index-map"
_ATOM_INDEX_MAP_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReconciledGeneratedInputs:
    """Verified generated input identity without creating new domain entities."""

    calculation: Calculation
    snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    system_context: VaspSystemContext
    prepared_poscar: PreparedPoscar
    prepared_kpoints: PreparedKPoints
    prepared_incar: PreparedIncar
    potcar_spec: PotcarSpec
    file_records: tuple[ManifestFileRecord, ...]
    preparation_hash: str


class GeneratedInputReconciliationError(VaspPreflightError):
    """Coded failure while reconciling one generated VASP input directory."""


def reconcile_generated_input_directory(
    *,
    folder: Path | str,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock | None,
) -> ReconciledGeneratedInputs:
    """Verify persisted generated inputs against exact live scientific identities."""

    root = Path(folder)
    if not root.is_dir():
        _fail(VaspFailClosedCode.INPUT_MANIFEST_MISSING, "generated input directory is missing")
    manifest_path = root / "input-manifest.json"
    if not manifest_path.is_file():
        _fail(VaspFailClosedCode.INPUT_MANIFEST_MISSING, "input-manifest.json is required")
    manifest = _read_json_object(manifest_path, VaspFailClosedCode.INPUT_MANIFEST_INVALID)
    _validate_manifest_identity(
        manifest=manifest,
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        project_lock=project_lock,
    )

    file_records = _validate_files(root=root, manifest=manifest)
    atom_map = _read_json_object(root / "atom-index-map.json", VaspFailClosedCode.ATOM_INDEX_MAP_INVALID)
    prepared_poscar = _reconcile_poscar(snapshot=snapshot, atom_map=atom_map, root=root)
    potcar_spec = _reconcile_potcar_spec(
        manifest=manifest,
        root=root,
        fingerprint=fingerprint,
        prepared_poscar=prepared_poscar,
    )
    prepared_kpoints = _reconcile_kpoints(
        manifest=manifest,
        root=root,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
    )
    validate_calculation_recipe_contract(
        calculation=calculation,
        system_context=system_context,
        project_lock=project_lock,
    )
    expected_incar = _compile_expected_incar(
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
    )
    actual_incar = (root / "INCAR").read_text(encoding="utf-8")
    if actual_incar != expected_incar.text:
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "INCAR bytes do not recompile from the exact MethodFingerprint",
        )

    preparation_hash = _recompute_preparation_hash(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_incar=expected_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
        files=file_records,
    )
    if _require_str(manifest, "preparation_hash") != preparation_hash:
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "manifest preparation_hash does not match reconciled inputs",
        )
    return ReconciledGeneratedInputs(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        prepared_incar=expected_incar,
        potcar_spec=potcar_spec,
        file_records=file_records,
        preparation_hash=preparation_hash,
    )


def _validate_manifest_identity(
    *,
    manifest: dict[str, Any],
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock | None,
) -> None:
    if manifest.get("format") != INPUT_MANIFEST_FORMAT or manifest.get("version") != INPUT_MANIFEST_VERSION:
        _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, "unsupported input manifest format/version")
    generator = _require_dict(manifest, "generator")
    if generator.get("name") != INPUT_GENERATOR_NAME or generator.get("version") != INPUT_GENERATOR_VERSION:
        _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, "unknown input generator identity")

    calculation_payload = _require_dict(manifest, "calculation")
    expected_calculation = {
        "id": str(calculation.id),
        "type": calculation.calculation_type.value,
        "engine": calculation.engine.value,
        "scientific_hash": scientific_hash(calculation),
    }
    if any(calculation_payload.get(key) != value for key, value in expected_calculation.items()):
        _fail(VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH, "Calculation identity mismatch")

    structure_payload = _require_dict(manifest, "structure")
    if structure_payload.get("snapshot_id") != str(snapshot.id) or structure_payload.get(
        "scientific_hash"
    ) != scientific_hash(snapshot):
        _fail(VaspFailClosedCode.SNAPSHOT_FINGERPRINT_MISMATCH, "StructureSnapshot identity mismatch")

    fingerprint_payload = _require_dict(manifest, "method_fingerprint")
    expected_fingerprint = {
        "id": str(fingerprint.id),
        "core_method_hash": fingerprint.core_method_hash,
        "protocol_hash": fingerprint.protocol_hash,
        "instance_hash": fingerprint.instance_hash,
    }
    if any(fingerprint_payload.get(key) != value for key, value in expected_fingerprint.items()):
        _fail(
            VaspFailClosedCode.SNAPSHOT_FINGERPRINT_MISMATCH,
            "MethodFingerprint identity mismatch",
        )

    recipe_payload = _require_dict(manifest, "recipe")
    expected_recipe = {
        "id": fingerprint.recipe.recipe_id,
        "version": fingerprint.recipe.version,
        "recipe_hash": fingerprint.recipe.recipe_hash,
    }
    if any(recipe_payload.get(key) != value for key, value in expected_recipe.items()):
        _fail(VaspFailClosedCode.RECIPE_PROTOCOL_CONFLICT, "Recipe identity mismatch")

    context_payload = _require_dict(manifest, "system_context")
    expected_axis = system_context.vacuum_axis.value if system_context.vacuum_axis is not None else None
    if context_payload.get("kind") != system_context.kind.value or context_payload.get(
        "vacuum_axis"
    ) != expected_axis:
        _fail(VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH, "SystemContext mismatch")

    validation = _require_dict(manifest, "validation")
    expected_lock_hash = project_lock.lock_hash if project_lock is not None else None
    if validation.get("status") != "passed" or validation.get("project_lock_hash") != expected_lock_hash:
        _fail(VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH, "ProjectNumericalLock mismatch")


def _validate_files(*, root: Path, manifest: dict[str, Any]) -> tuple[ManifestFileRecord, ...]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, "manifest files must be a non-empty list")
    records: list[ManifestFileRecord] = []
    roles: set[str] = set()
    basenames: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, "manifest file entry must be an object")
        role = _require_str(raw, "role")
        relative_path = _require_str(raw, "relative_path")
        sha256 = _require_str(raw, "sha256")
        size_bytes = raw.get("size_bytes")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, "manifest file size is invalid")
        artifact_type = raw.get("artifact_type")
        expected_name = _EXPECTED_ROLE_FILENAMES.get(role)
        if expected_name is None:
            _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, f"unknown generated input role: {role}")
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts or path.name != expected_name:
            _fail(VaspFailClosedCode.INPUT_FILE_PATH_INVALID, f"unsafe path for role {role}")
        if role in roles or path.name in basenames:
            _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, "duplicate generated input role/path")
        roles.add(role)
        basenames.add(path.name)
        actual_path = root / path.name
        if not actual_path.is_file():
            _fail(VaspFailClosedCode.INPUT_FILE_MISSING, f"missing generated file {path.name}")
        raw_bytes = actual_path.read_bytes()
        if len(raw_bytes) != size_bytes:
            _fail(VaspFailClosedCode.INPUT_FILE_SIZE_MISMATCH, f"size mismatch for {path.name}")
        if hashlib.sha256(raw_bytes).hexdigest() != sha256:
            _fail(VaspFailClosedCode.INPUT_FILE_HASH_MISMATCH, f"hash mismatch for {path.name}")
        try:
            from ecatvasp.domain import ArtifactType

            parsed_artifact_type = ArtifactType(str(artifact_type))
            record = ManifestFileRecord(
                role=role,
                artifact_type=parsed_artifact_type,
                relative_path=relative_path,
                sha256=sha256,
                size_bytes=size_bytes,
            )
        except (ValueError, TypeError) as error:
            raise GeneratedInputReconciliationError(
                VaspFailClosedCode.INPUT_MANIFEST_INVALID,
                f"invalid artifact type for role {role}",
            ) from error
        records.append(record)
    required = {"incar", "poscar", "potcar_spec", "atom_index_map"}
    if not required.issubset(roles):
        _fail(VaspFailClosedCode.INPUT_FILE_MISSING, "manifest is missing required generated inputs")
    return tuple(sorted(records, key=lambda item: item.role))


def _reconcile_poscar(
    *,
    snapshot: StructureSnapshot,
    atom_map: dict[str, Any],
    root: Path,
) -> PreparedPoscar:
    if atom_map.get("format") != _ATOM_INDEX_MAP_FORMAT or atom_map.get("version") != _ATOM_INDEX_MAP_VERSION:
        _fail(VaspFailClosedCode.ATOM_INDEX_MAP_INVALID, "unsupported atom-index-map format/version")
    if atom_map.get("structure_snapshot_id") != str(snapshot.id) or atom_map.get(
        "structure_sha256"
    ) != scientific_hash(snapshot):
        _fail(VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH, "atom map targets another snapshot")
    entries = atom_map.get("entries")
    if not isinstance(entries, list) or len(entries) != len(snapshot.sites):
        _fail(VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH, "atom map entry count mismatch")

    snapshot_by_uid = {str(site.atom_uid): (index, site) for index, site in enumerate(snapshot.sites)}
    seen_uids: set[str] = set()
    selective: list[AtomSelectiveFlags] = []
    any_selective = False
    for expected_poscar_index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            _fail(VaspFailClosedCode.ATOM_INDEX_MAP_INVALID, "atom map entry must be an object")
        uid_text = _require_str(raw, "atom_uid")
        if uid_text in seen_uids or uid_text not in snapshot_by_uid:
            _fail(VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH, "unknown/duplicate atom_uid")
        seen_uids.add(uid_text)
        snapshot_index, site = snapshot_by_uid[uid_text]
        if raw.get("element") != site.element or raw.get("snapshot_index") != snapshot_index:
            _fail(VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH, "atom UID metadata mismatch")
        if raw.get("poscar_index") != expected_poscar_index or raw.get("vasp_ordinal") != expected_poscar_index + 1:
            _fail(VaspFailClosedCode.ATOM_INDEX_MAP_INVALID, "POSCAR index mapping is not contiguous")
        flags = raw.get("selective_dynamics")
        if flags is not None:
            if (
                not isinstance(flags, list)
                or len(flags) != 3
                or any(not isinstance(value, bool) for value in flags)
            ):
                _fail(VaspFailClosedCode.ATOM_INDEX_MAP_INVALID, "invalid selective-dynamics flags")
            any_selective = True
            selective.append(
                AtomSelectiveFlags(AtomUid(UUID(uid_text)), (flags[0], flags[1], flags[2]))
            )
    if seen_uids != set(snapshot_by_uid):
        _fail(VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH, "atom map does not cover snapshot")
    if any_selective and len(selective) != len(entries):
        _fail(
            VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
            "selective-dynamics flags must be present for every atom or none",
        )
    dynamics = (
        None
        if not any_selective
        else UidSelectiveDynamics(default_flags=(False, False, False), overrides=tuple(selective))
    )
    prepared = prepare_poscar(snapshot, selective_dynamics=dynamics)
    actual_text = (root / "POSCAR").read_text(encoding="utf-8")
    if actual_text != prepared.text or atom_map.get("poscar_sha256") != prepared.sha256:
        _fail(VaspFailClosedCode.GENERATED_POSCAR_MISMATCH, "POSCAR does not roundtrip from atom_uid map")
    species_order = atom_map.get("species_order")
    species_counts = atom_map.get("species_counts")
    if species_order != list(prepared.species_order) or species_counts != list(prepared.species_counts):
        _fail(VaspFailClosedCode.ATOM_INDEX_MAP_INVALID, "species order/count mismatch")
    return prepared


def _reconcile_potcar_spec(
    *,
    manifest: dict[str, Any],
    root: Path,
    fingerprint: MethodFingerprint,
    prepared_poscar: PreparedPoscar,
) -> PotcarSpec:
    payload = _require_dict(manifest, "potcar_spec")
    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        _fail(VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH, "missing POTCAR metadata")
    entries: list[PotcarSpecEntry] = []
    try:
        for raw in entries_raw:
            if not isinstance(raw, dict):
                raise TypeError("POTCAR entry is not an object")
            entries.append(
                PotcarSpecEntry(
                    element=_require_str(raw, "element"),
                    symbol=_require_str(raw, "symbol"),
                    family=_require_str(raw, "family"),
                    titel=_require_str(raw, "titel"),
                    zval=float(raw["zval"]),
                    enmax_ev=float(raw["enmax_ev"]),
                    sha256=_require_str(raw, "sha256"),
                )
            )
    except (KeyError, TypeError, ValueError) as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
            "invalid POTCAR metadata",
        ) from error
    text = (root / "POTCAR.spec").read_text(encoding="utf-8")
    spec = PotcarSpec(
        core_method_hash=fingerprint.core_method_hash,
        entries=tuple(entries),
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    if payload.get("species_order") != list(spec.species_order):
        _fail(VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH, "POTCAR species order mismatch")
    if spec.species_order != prepared_poscar.species_order:
        _fail(VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH, "POTCAR/POSCAR species mismatch")
    identities = {item.element: item for item in fingerprint.method.potcars}
    for entry in spec.entries:
        identity = identities.get(entry.element)
        if identity is None or identity.symbol != entry.symbol or identity.sha256 != entry.sha256:
            _fail(VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH, "POTCAR identity mismatch")
        if entry.family != fingerprint.method.potcar_family:
            _fail(VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH, "POTCAR family mismatch")
    preparations = _require_dict(manifest, "preparations")
    if preparations.get("potcar_spec_sha256") != spec.sha256 or preparations.get(
        "potcar_metadata_hash"
    ) != spec.metadata_hash:
        _fail(VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH, "POTCAR hashes mismatch")
    return spec


def _reconcile_kpoints(
    *,
    manifest: dict[str, Any],
    root: Path,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
) -> PreparedKPoints:
    centering = _protocol_kpoint_centering(fingerprint.protocol)
    prepared = prepare_kpoints(
        snapshot,
        policy=fingerprint.protocol.kpoints,
        system_context=system_context,
        centering=centering,
    )
    validate_protocol_kpoint_contract(protocol=fingerprint.protocol, prepared=prepared)
    payload = _require_dict(manifest, "kpoints")
    expected_payload = {
        "policy_kind": prepared.policy.kind.value,
        "mesh": list(prepared.mesh),
        "centering": prepared.centering.value,
        "uses_kpoints_file": prepared.uses_kpoints_file,
        "kspacing_inv_angstrom": prepared.kspacing_inv_angstrom,
        "kgamma": prepared.kgamma,
    }
    if any(payload.get(key) != value for key, value in expected_payload.items()):
        _fail(VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH, "k-point identity mismatch")
    preparations = _require_dict(manifest, "preparations")
    if preparations.get("kpoints_identity_hash") != prepared.identity_hash:
        _fail(VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH, "k-point plan hash mismatch")
    kpoints_path = root / "KPOINTS"
    if prepared.uses_kpoints_file:
        if not kpoints_path.is_file() or kpoints_path.read_text(encoding="utf-8") != prepared.text:
            _fail(VaspFailClosedCode.INPUT_FILE_HASH_MISMATCH, "KPOINTS does not match compiled plan")
    elif kpoints_path.exists():
        _fail(VaspFailClosedCode.KSPACING_WITH_KPOINTS_CONFLICT, "KSPACING input must not contain KPOINTS")
    return prepared


def _compile_expected_incar(
    *,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
) -> PreparedIncar:
    recipe = fingerprint.recipe
    if recipe.recipe_id in _FREQUENCY_RECIPES:
        if project_lock is None:
            _fail(VaspFailClosedCode.ENCUT_NOT_LOCKED, "frequency reconciliation requires project lock")
        validate_frequency_prepared_poscar(prepared_poscar=prepared_poscar, fingerprint=fingerprint)
        return prepare_frequency_incar(
            snapshot=snapshot,
            method=fingerprint.method,
            protocol=fingerprint.protocol,
            recipe=recipe,
            system_context=system_context,
            prepared_poscar=prepared_poscar,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=potcar_spec,
            project_lock=project_lock,
        )
    if recipe.recipe_id in _ANALYSIS_RECIPES:
        if project_lock is None:
            _fail(VaspFailClosedCode.ENCUT_NOT_LOCKED, "analysis prerequisite reconciliation requires project lock")
        return prepare_analysis_prerequisite_incar(
            snapshot=snapshot,
            method=fingerprint.method,
            protocol=fingerprint.protocol,
            recipe=recipe,
            system_context=system_context,
            prepared_poscar=prepared_poscar,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=potcar_spec,
            project_lock=project_lock,
        )
    return prepare_incar(
        snapshot=snapshot,
        method=fingerprint.method,
        protocol=fingerprint.protocol,
        recipe=recipe,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
    )


def _recompute_preparation_hash(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_incar: PreparedIncar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
    files: tuple[ManifestFileRecord, ...],
) -> str:
    from ecatvasp.domain import canonical_sha256

    return canonical_sha256(
        {
            "calculation_hash": scientific_hash(calculation),
            "snapshot_hash": scientific_hash(snapshot),
            "fingerprint_instance_hash": fingerprint.instance_hash,
            "recipe_hash": fingerprint.recipe.recipe_hash,
            "system_kind": system_context.kind,
            "vacuum_axis": system_context.vacuum_axis,
            "prepared_poscar_sha256": prepared_poscar.sha256,
            "prepared_incar_sha256": prepared_incar.sha256,
            "prepared_kpoints_identity_hash": prepared_kpoints.identity_hash,
            "potcar_metadata_hash": potcar_spec.metadata_hash,
            "project_lock_hash": project_lock.lock_hash if project_lock is not None else None,
            "files": files,
        }
    )


def _protocol_kpoint_centering(protocol: ProtocolDefinition) -> KPointCentering:
    matches = tuple(
        item for item in protocol.extra_parameters if item.name == ECATVASP_KPOINT_CENTERING
    )
    if len(matches) != 1 or not isinstance(matches[0].value, str):
        _fail(VaspFailClosedCode.ILLEGAL_KPOINT_CENTERING, "missing/invalid k-point centering")
    try:
        return KPointCentering(matches[0].value)
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.ILLEGAL_KPOINT_CENTERING,
            "invalid k-point centering",
        ) from error


def _read_json_object(path: Path, code: VaspFailClosedCode) -> dict[str, Any]:
    if not path.is_file():
        _fail(code, f"missing {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GeneratedInputReconciliationError(code, f"cannot parse {path.name}") from error
    if not isinstance(payload, dict):
        _fail(code, f"{path.name} must contain a JSON object")
    return payload


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, f"manifest {key} must be an object")
    return value


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        _fail(VaspFailClosedCode.INPUT_MANIFEST_INVALID, f"{key} must be a non-empty string")
    return value


def _fail(code: VaspFailClosedCode, message: str) -> None:
    try:
        fail_closed(code, message)
    except VaspPreflightError as error:
        raise GeneratedInputReconciliationError(error.code, message) from error
