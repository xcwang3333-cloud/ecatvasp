"""Manifest-aware reconciliation for immutable ECatVASP-generated VASP inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast
from uuid import UUID

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    MethodFingerprint,
    SpinTreatment,
    StructureSnapshot,
    canonical_sha256,
)
from ecatvasp.domain.ids import AtomUid
from ecatvasp.domain.method import ProtocolDefinition
from ecatvasp.provenance import scientific_hash
from ecatvasp.vasp.analysis_prerequisites import prepare_analysis_prerequisite_incar
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.frequency import (
    prepare_frequency_incar,
    validate_frequency_prepared_poscar,
)
from ecatvasp.vasp.incar import AtomMagmom, PreparedIncar, UidMagmom, prepare_incar
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
from ecatvasp.vasp.preflight import VaspFailClosedCode, VaspPreflightError
from ecatvasp.vasp.recipes import (
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    validate_calculation_recipe_contract,
)

JsonObject = dict[str, object]

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
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_MISSING,
            "generated input directory is missing",
        )
    manifest = _read_json_object(
        root / "input-manifest.json",
        VaspFailClosedCode.INPUT_MANIFEST_MISSING,
    )
    _validate_manifest_identity(
        manifest=manifest,
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        project_lock=project_lock,
    )

    file_records = _validate_files(
        root=root,
        manifest=manifest,
        calculation=calculation,
    )
    atom_map = _read_json_object(
        root / "atom-index-map.json",
        VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
    )
    prepared_poscar = _reconcile_poscar(
        snapshot=snapshot,
        atom_map=atom_map,
        root=root,
    )
    potcar_spec = _reconcile_potcar_spec(
        manifest=manifest,
        root=root,
        fingerprint=fingerprint,
        prepared_poscar=prepared_poscar,
    )
    prepared_kpoints = _reconcile_kpoints(
        manifest=manifest,
        file_records=file_records,
        root=root,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
    )
    try:
        validate_calculation_recipe_contract(
            calculation=calculation,
            system_context=system_context,
            project_lock=project_lock,
        )
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.RECIPE_PROTOCOL_CONFLICT,
            str(error),
        ) from error

    magmom = _recover_magmom(
        manifest=manifest,
        fingerprint=fingerprint,
        prepared_poscar=prepared_poscar,
    )
    prepared_incar = _compile_expected_incar(
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
        magmom=magmom,
    )
    if (root / "INCAR").read_text(encoding="utf-8") != prepared_incar.text:
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "INCAR bytes do not recompile from the exact MethodFingerprint",
        )
    _validate_manifest_preparations(
        manifest=manifest,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        file_records=file_records,
    )
    _validate_effective_parameters(manifest=manifest, prepared_incar=prepared_incar)

    preparation_hash = _recompute_preparation_hash(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
        files=file_records,
    )
    if _require_str(manifest.get("preparation_hash"), "preparation_hash") != preparation_hash:
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
        prepared_incar=prepared_incar,
        potcar_spec=potcar_spec,
        file_records=file_records,
        preparation_hash=preparation_hash,
    )


def _validate_manifest_identity(
    *,
    manifest: JsonObject,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock | None,
) -> None:
    if (
        manifest.get("format") != INPUT_MANIFEST_FORMAT
        or manifest.get("version") != INPUT_MANIFEST_VERSION
    ):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            "unsupported input manifest format/version",
        )
    generator = _require_object(manifest.get("generator"), "generator")
    if (
        generator.get("name") != INPUT_GENERATOR_NAME
        or generator.get("version") != INPUT_GENERATOR_VERSION
    ):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            "unknown input generator identity",
        )

    calculation_payload = _require_object(manifest.get("calculation"), "calculation")
    expected_calculation: JsonObject = {
        "id": str(calculation.id),
        "type": calculation.calculation_type.value,
        "engine": calculation.engine.value,
        "scientific_hash": scientific_hash(calculation),
    }
    if any(
        calculation_payload.get(key) != value
        for key, value in expected_calculation.items()
    ):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "Calculation identity mismatch",
        )

    structure_payload = _require_object(manifest.get("structure"), "structure")
    if (
        structure_payload.get("snapshot_id") != str(snapshot.id)
        or structure_payload.get("scientific_hash") != scientific_hash(snapshot)
    ):
        _fail(
            VaspFailClosedCode.SNAPSHOT_FINGERPRINT_MISMATCH,
            "StructureSnapshot identity mismatch",
        )

    fingerprint_payload = _require_object(
        manifest.get("method_fingerprint"),
        "method_fingerprint",
    )
    expected_fingerprint: JsonObject = {
        "id": str(fingerprint.id),
        "core_method_hash": fingerprint.core_method_hash,
        "protocol_hash": fingerprint.protocol_hash,
        "instance_hash": fingerprint.instance_hash,
    }
    if any(
        fingerprint_payload.get(key) != value
        for key, value in expected_fingerprint.items()
    ):
        _fail(
            VaspFailClosedCode.SNAPSHOT_FINGERPRINT_MISMATCH,
            "MethodFingerprint identity mismatch",
        )

    recipe_payload = _require_object(manifest.get("recipe"), "recipe")
    expected_recipe: JsonObject = {
        "id": fingerprint.recipe.recipe_id,
        "version": fingerprint.recipe.version,
        "recipe_hash": fingerprint.recipe.recipe_hash,
    }
    if any(recipe_payload.get(key) != value for key, value in expected_recipe.items()):
        _fail(
            VaspFailClosedCode.RECIPE_PROTOCOL_CONFLICT,
            "Recipe identity mismatch",
        )

    context_payload = _require_object(manifest.get("system_context"), "system_context")
    expected_axis = (
        system_context.vacuum_axis.value
        if system_context.vacuum_axis is not None
        else None
    )
    if (
        context_payload.get("kind") != system_context.kind.value
        or context_payload.get("vacuum_axis") != expected_axis
    ):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "SystemContext mismatch",
        )

    validation = _require_object(manifest.get("validation"), "validation")
    expected_lock_hash = project_lock.lock_hash if project_lock is not None else None
    if (
        validation.get("status") != "passed"
        or validation.get("project_lock_hash") != expected_lock_hash
    ):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "ProjectNumericalLock mismatch",
        )


def _validate_files(
    *,
    root: Path,
    manifest: JsonObject,
    calculation: Calculation,
) -> tuple[ManifestFileRecord, ...]:
    raw_files = _require_list(manifest.get("files"), "files")
    if not raw_files:
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            "manifest files must not be empty",
        )
    records: list[ManifestFileRecord] = []
    roles: set[str] = set()
    basenames: set[str] = set()
    for raw_value in raw_files:
        raw = _require_object(raw_value, "file entry")
        role = _require_str(raw.get("role"), "role")
        relative_path = _require_str(raw.get("relative_path"), "relative_path")
        sha256 = _require_str(raw.get("sha256"), "sha256")
        size_bytes = _require_int(raw.get("size_bytes"), "size_bytes")
        if size_bytes < 0:
            _fail(
                VaspFailClosedCode.INPUT_MANIFEST_INVALID,
                "manifest file size must not be negative",
            )
        expected_name = _EXPECTED_ROLE_FILENAMES.get(role)
        if expected_name is None:
            _fail(
                VaspFailClosedCode.INPUT_MANIFEST_INVALID,
                f"unknown generated input role: {role}",
            )
        path = PurePosixPath(relative_path)
        expected_path = PurePosixPath(
            "calculations",
            str(calculation.id),
            "inputs",
            expected_name,
        )
        if path != expected_path or path.is_absolute() or ".." in path.parts:
            _fail(
                VaspFailClosedCode.INPUT_FILE_PATH_INVALID,
                f"unexpected generated path for role {role}",
            )
        if role in roles or path.name in basenames:
            _fail(
                VaspFailClosedCode.INPUT_MANIFEST_INVALID,
                "duplicate generated input role/path",
            )
        roles.add(role)
        basenames.add(path.name)
        actual_path = root / path.name
        if not actual_path.is_file():
            _fail(
                VaspFailClosedCode.INPUT_FILE_MISSING,
                f"missing generated file {path.name}",
            )
        raw_bytes = actual_path.read_bytes()
        if len(raw_bytes) != size_bytes:
            _fail(
                VaspFailClosedCode.INPUT_FILE_SIZE_MISMATCH,
                f"size mismatch for {path.name}",
            )
        if hashlib.sha256(raw_bytes).hexdigest() != sha256:
            _fail(
                VaspFailClosedCode.INPUT_FILE_HASH_MISMATCH,
                f"hash mismatch for {path.name}",
            )
        artifact_type_value = _require_str(raw.get("artifact_type"), "artifact_type")
        try:
            artifact_type = ArtifactType(artifact_type_value)
        except ValueError as error:
            raise GeneratedInputReconciliationError(
                VaspFailClosedCode.INPUT_MANIFEST_INVALID,
                f"invalid artifact type for role {role}",
            ) from error
        try:
            record = ManifestFileRecord(
                role=role,
                artifact_type=artifact_type,
                relative_path=relative_path,
                sha256=sha256,
                size_bytes=size_bytes,
            )
        except ValueError as error:
            raise GeneratedInputReconciliationError(
                VaspFailClosedCode.INPUT_MANIFEST_INVALID,
                f"invalid manifest record for role {role}",
            ) from error
        records.append(record)

    required = {"incar", "poscar", "potcar_spec", "atom_index_map"}
    if not required.issubset(roles):
        _fail(
            VaspFailClosedCode.INPUT_FILE_MISSING,
            "manifest is missing required generated inputs",
        )
    kpoint_payload = _require_object(manifest.get("kpoints"), "kpoints")
    uses_kpoints_file = _require_bool(
        kpoint_payload.get("uses_kpoints_file"),
        "uses_kpoints_file",
    )
    if uses_kpoints_file != ("kpoints" in roles):
        _fail(
            VaspFailClosedCode.KSPACING_WITH_KPOINTS_CONFLICT,
            "manifest KPOINTS presence disagrees with k-point policy",
        )
    return tuple(sorted(records, key=lambda item: item.role))


def _reconcile_poscar(
    *,
    snapshot: StructureSnapshot,
    atom_map: JsonObject,
    root: Path,
) -> PreparedPoscar:
    if (
        atom_map.get("format") != _ATOM_INDEX_MAP_FORMAT
        or atom_map.get("version") != _ATOM_INDEX_MAP_VERSION
    ):
        _fail(
            VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
            "unsupported atom-index-map format/version",
        )
    if (
        atom_map.get("structure_snapshot_id") != str(snapshot.id)
        or atom_map.get("structure_sha256") != scientific_hash(snapshot)
    ):
        _fail(
            VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH,
            "atom map targets another snapshot",
        )
    entries = _require_list(atom_map.get("entries"), "entries")
    if len(entries) != len(snapshot.sites):
        _fail(
            VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH,
            "atom map entry count mismatch",
        )

    snapshot_by_uid = {
        str(site.atom_uid): (index, site)
        for index, site in enumerate(snapshot.sites)
    }
    seen_uids: set[str] = set()
    selective: list[AtomSelectiveFlags] = []
    any_selective = False
    for expected_poscar_index, raw_value in enumerate(entries):
        raw = _require_object(raw_value, "atom map entry")
        uid_text = _require_str(raw.get("atom_uid"), "atom_uid")
        if uid_text in seen_uids or uid_text not in snapshot_by_uid:
            _fail(
                VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH,
                "unknown/duplicate atom_uid",
            )
        seen_uids.add(uid_text)
        snapshot_index, site = snapshot_by_uid[uid_text]
        if raw.get("element") != site.element or raw.get("snapshot_index") != snapshot_index:
            _fail(
                VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH,
                "atom UID metadata mismatch",
            )
        if (
            raw.get("poscar_index") != expected_poscar_index
            or raw.get("vasp_ordinal") != expected_poscar_index + 1
        ):
            _fail(
                VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
                "POSCAR index mapping is not contiguous",
            )
        flags_value = raw.get("selective_dynamics")
        if flags_value is not None:
            flags = _require_list(flags_value, "selective_dynamics")
            if len(flags) != 3 or any(not isinstance(value, bool) for value in flags):
                _fail(
                    VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
                    "invalid selective-dynamics flags",
                )
            bool_flags = cast(tuple[bool, bool, bool], tuple(flags))
            any_selective = True
            selective.append(
                AtomSelectiveFlags(
                    _parse_atom_uid(uid_text),
                    bool_flags,
                )
            )
    if seen_uids != set(snapshot_by_uid):
        _fail(
            VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH,
            "atom map does not cover snapshot",
        )
    if any_selective and len(selective) != len(entries):
        _fail(
            VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
            "selective-dynamics flags must be present for every atom or none",
        )
    dynamics = (
        None
        if not any_selective
        else UidSelectiveDynamics(
            default_flags=(False, False, False),
            overrides=tuple(selective),
        )
    )
    prepared = prepare_poscar(snapshot, selective_dynamics=dynamics)
    if (
        (root / "POSCAR").read_text(encoding="utf-8") != prepared.text
        or atom_map.get("poscar_sha256") != prepared.sha256
    ):
        _fail(
            VaspFailClosedCode.GENERATED_POSCAR_MISMATCH,
            "POSCAR does not roundtrip from atom_uid map",
        )
    if (
        atom_map.get("species_order") != list(prepared.species_order)
        or atom_map.get("species_counts") != list(prepared.species_counts)
    ):
        _fail(
            VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
            "species order/count mismatch",
        )
    return prepared


def _reconcile_potcar_spec(
    *,
    manifest: JsonObject,
    root: Path,
    fingerprint: MethodFingerprint,
    prepared_poscar: PreparedPoscar,
) -> PotcarSpec:
    payload = _require_object(manifest.get("potcar_spec"), "potcar_spec")
    entries_raw = _require_list(payload.get("entries"), "potcar_spec.entries")
    if not entries_raw:
        _fail(
            VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
            "missing POTCAR metadata",
        )
    entries: list[PotcarSpecEntry] = []
    for raw_value in entries_raw:
        raw = _require_object(raw_value, "POTCAR entry")
        try:
            entry = PotcarSpecEntry(
                element=_require_str(raw.get("element"), "element"),
                symbol=_require_str(raw.get("symbol"), "symbol"),
                family=_require_str(raw.get("family"), "family"),
                titel=_require_str(raw.get("titel"), "titel"),
                zval=_require_number(raw.get("zval"), "zval"),
                enmax_ev=_require_number(raw.get("enmax_ev"), "enmax_ev"),
                sha256=_require_str(raw.get("sha256"), "sha256"),
            )
        except ValueError as error:
            raise GeneratedInputReconciliationError(
                VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
                "invalid POTCAR metadata",
            ) from error
        entries.append(entry)
    text = (root / "POTCAR.spec").read_text(encoding="utf-8")
    try:
        spec = PotcarSpec(
            core_method_hash=fingerprint.core_method_hash,
            entries=tuple(entries),
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
            "invalid POTCAR.spec content",
        ) from error
    if payload.get("species_order") != list(spec.species_order):
        _fail(
            VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
            "POTCAR species order mismatch",
        )
    if spec.species_order != prepared_poscar.species_order:
        _fail(
            VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
            "POTCAR/POSCAR species mismatch",
        )
    identities = {item.element: item for item in fingerprint.method.potcars}
    for entry in spec.entries:
        identity = identities.get(entry.element)
        if (
            identity is None
            or identity.symbol != entry.symbol
            or identity.sha256 != entry.sha256
        ):
            _fail(
                VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
                "POTCAR identity mismatch",
            )
        if entry.family != fingerprint.method.potcar_family:
            _fail(
                VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
                "POTCAR family mismatch",
            )
    return spec


def _reconcile_kpoints(
    *,
    manifest: JsonObject,
    file_records: tuple[ManifestFileRecord, ...],
    root: Path,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
) -> PreparedKPoints:
    centering = _protocol_kpoint_centering(fingerprint.protocol)
    try:
        prepared = prepare_kpoints(
            snapshot,
            policy=fingerprint.protocol.kpoints,
            system_context=system_context,
            centering=centering,
        )
        validate_protocol_kpoint_contract(
            protocol=fingerprint.protocol,
            prepared=prepared,
        )
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.RECIPE_PROTOCOL_CONFLICT,
            str(error),
        ) from error
    payload = _require_object(manifest.get("kpoints"), "kpoints")
    expected_payload: JsonObject = {
        "policy_kind": prepared.policy.kind.value,
        "mesh": list(prepared.mesh),
        "centering": prepared.centering.value,
        "uses_kpoints_file": prepared.uses_kpoints_file,
        "kspacing_inv_angstrom": prepared.kspacing_inv_angstrom,
        "kgamma": prepared.kgamma,
    }
    if any(payload.get(key) != value for key, value in expected_payload.items()):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "k-point identity mismatch",
        )
    roles = {item.role for item in file_records}
    kpoints_path = root / "KPOINTS"
    if prepared.uses_kpoints_file:
        if "kpoints" not in roles or not kpoints_path.is_file():
            _fail(
                VaspFailClosedCode.INPUT_FILE_MISSING,
                "compiled k-point plan requires KPOINTS",
            )
        if kpoints_path.read_text(encoding="utf-8") != prepared.text:
            _fail(
                VaspFailClosedCode.INPUT_FILE_HASH_MISMATCH,
                "KPOINTS does not match compiled plan",
            )
    elif kpoints_path.exists() or "kpoints" in roles:
        _fail(
            VaspFailClosedCode.KSPACING_WITH_KPOINTS_CONFLICT,
            "KSPACING input must not contain KPOINTS",
        )
    return prepared


def _recover_magmom(
    *,
    manifest: JsonObject,
    fingerprint: MethodFingerprint,
    prepared_poscar: PreparedPoscar,
) -> UidMagmom | None:
    treatment = fingerprint.method.spin_treatment
    if treatment is SpinTreatment.UNPOLARIZED:
        return None
    parameters = _require_list(manifest.get("effective_parameters"), "effective_parameters")
    rendered: str | None = None
    for raw_value in parameters:
        raw = _require_object(raw_value, "effective parameter")
        if raw.get("name") == "MAGMOM":
            rendered = _require_str(raw.get("value"), "MAGMOM")
            break
    if rendered is None:
        _fail(
            VaspFailClosedCode.MAGMOM_UID_MISSING,
            "spin-polarized manifest is missing MAGMOM",
        )
    try:
        values = tuple(float(item) for item in rendered.split())
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.MAGMOM_UID_MISSING,
            "manifest MAGMOM contains non-numeric values",
        ) from error
    width = 1 if treatment is SpinTreatment.COLLINEAR else 3
    entries = prepared_poscar.index_map.entries
    if len(values) != len(entries) * width:
        _fail(
            VaspFailClosedCode.MAGMOM_UID_MISSING,
            "manifest MAGMOM length does not match POSCAR atom mapping",
        )
    return UidMagmom(
        tuple(
            AtomMagmom(
                entry.atom_uid,
                tuple(values[index * width : (index + 1) * width]),
            )
            for index, entry in enumerate(entries)
        )
    )


def _compile_expected_incar(
    *,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
    magmom: UidMagmom | None,
) -> PreparedIncar:
    recipe = fingerprint.recipe
    try:
        if recipe.recipe_id in _FREQUENCY_RECIPES:
            if project_lock is None:
                _fail(
                    VaspFailClosedCode.ENCUT_NOT_LOCKED,
                    "frequency reconciliation requires project lock",
                )
            validate_frequency_prepared_poscar(
                prepared_poscar=prepared_poscar,
                fingerprint=fingerprint,
            )
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
                magmom=magmom,
            )
        if recipe.recipe_id in _ANALYSIS_RECIPES:
            if project_lock is None:
                _fail(
                    VaspFailClosedCode.ENCUT_NOT_LOCKED,
                    "analysis prerequisite reconciliation requires project lock",
                )
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
                magmom=magmom,
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
            magmom=magmom,
        )
    except GeneratedInputReconciliationError:
        raise
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.RECIPE_PROTOCOL_CONFLICT,
            f"generated INCAR no longer satisfies the compiler contract: {error}",
        ) from error


def _validate_manifest_preparations(
    *,
    manifest: JsonObject,
    prepared_poscar: PreparedPoscar,
    prepared_incar: PreparedIncar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    file_records: tuple[ManifestFileRecord, ...],
) -> None:
    preparations = _require_object(manifest.get("preparations"), "preparations")
    atom_map_record = next(
        (item for item in file_records if item.role == "atom_index_map"),
        None,
    )
    if atom_map_record is None:
        _fail(
            VaspFailClosedCode.INPUT_FILE_MISSING,
            "atom-index-map record is missing",
        )
    expected: JsonObject = {
        "poscar_sha256": prepared_poscar.sha256,
        "incar_sha256": prepared_incar.sha256,
        "kpoints_identity_hash": prepared_kpoints.identity_hash,
        "potcar_spec_sha256": potcar_spec.sha256,
        "potcar_metadata_hash": potcar_spec.metadata_hash,
        "atom_index_map_sha256": atom_map_record.sha256,
    }
    if any(preparations.get(key) != value for key, value in expected.items()):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "manifest preparation identities do not match reconciled inputs",
        )


def _validate_effective_parameters(
    *,
    manifest: JsonObject,
    prepared_incar: PreparedIncar,
) -> None:
    actual = _require_list(manifest.get("effective_parameters"), "effective_parameters")
    expected: list[object] = [
        {
            "name": item.name,
            "value": item.value,
            "source": item.source.value,
        }
        for item in prepared_incar.parameters
    ]
    if actual != expected:
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
            "manifest effective parameters do not match recompiled INCAR",
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
        item
        for item in protocol.extra_parameters
        if item.name == ECATVASP_KPOINT_CENTERING
    )
    if len(matches) != 1:
        _fail(
            VaspFailClosedCode.ILLEGAL_KPOINT_CENTERING,
            "missing/ambiguous k-point centering",
        )
    value = matches[0].value
    if not isinstance(value, str):
        _fail(
            VaspFailClosedCode.ILLEGAL_KPOINT_CENTERING,
            "k-point centering must be a string",
        )
    try:
        return KPointCentering(value)
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.ILLEGAL_KPOINT_CENTERING,
            "invalid k-point centering",
        ) from error


def _read_json_object(path: Path, missing_code: VaspFailClosedCode) -> JsonObject:
    if not path.is_file():
        _fail(missing_code, f"missing {path.name}")
    try:
        payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            f"cannot parse {path.name}",
        ) from error
    return _require_object(payload, path.name)


def _require_object(value: object, field_name: str) -> JsonObject:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            f"{field_name} must be a JSON object with string keys",
        )
    return cast(JsonObject, value)


def _require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            f"{field_name} must be a JSON list",
        )
    return cast(list[object], value)


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            f"{field_name} must be a non-empty string",
        )
    return value


def _require_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            f"{field_name} must be an integer",
        )
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            f"{field_name} must be boolean",
        )
    return value


def _require_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            VaspFailClosedCode.INPUT_MANIFEST_INVALID,
            f"{field_name} must be numeric",
        )
    return float(value)


def _parse_atom_uid(value: str) -> AtomUid:
    try:
        return AtomUid(UUID(value))
    except ValueError as error:
        raise GeneratedInputReconciliationError(
            VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH,
            "atom_uid is not a valid UUID",
        ) from error


def _fail(code: VaspFailClosedCode, message: str) -> NoReturn:
    raise GeneratedInputReconciliationError(code, message)
