"""Immutable materialization of prepared VASP scientific inputs and manifests."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationProducerRef,
    MethodFingerprint,
    RetrievalPolicy,
    StructureSnapshot,
    canonical_sha256,
)
from ecatvasp.domain.ids import ArtifactId, CalculationId
from ecatvasp.domain.method import RecipeIdentity
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.incar import PreparedIncar
from ecatvasp.vasp.kpoints import PreparedKPoints
from ecatvasp.vasp.poscar import PreparedPoscar
from ecatvasp.vasp.potcar import PotcarSpec

INPUT_MANIFEST_FORMAT = "ecatvasp-v03-input-manifest"
INPUT_MANIFEST_VERSION = 1
INPUT_GENERATOR_NAME = "ecatvasp.vasp.input-materializer"
INPUT_GENERATOR_VERSION = "1"


class InputMaterializationError(RuntimeError):
    """Raised when prepared VASP inputs cannot be materialized immutably."""


@dataclass(frozen=True, slots=True)
class ManifestFileRecord:
    """One redistribution-safe generated input recorded by the manifest."""

    role: str
    artifact_type: ArtifactType
    relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("manifest file role must not be blank")
        _validate_relative_path(self.relative_path)
        _validate_sha256(self.sha256, "sha256")
        if self.size_bytes < 0:
            raise ValueError("manifest file size_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class InputManifest:
    """Deterministic manifest pinning one exact set of VASP scientific inputs."""

    calculation_id: CalculationId
    files: tuple[ManifestFileRecord, ...]
    payload: dict[str, object]
    text: str
    sha256: str
    preparation_hash: str

    def __post_init__(self) -> None:
        roles = tuple(item.role for item in self.files)
        if roles != tuple(sorted(roles)):
            raise ValueError("manifest file records must be sorted by role")
        if len(roles) != len(set(roles)):
            raise ValueError("manifest file roles must be unique")
        expected_text = _json_text(self.payload)
        if self.text != expected_text:
            raise ValueError("InputManifest text does not match payload")
        expected_sha = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 != expected_sha:
            raise ValueError("InputManifest sha256 does not match text")
        _validate_sha256(self.preparation_hash, "preparation_hash")


@dataclass(frozen=True, slots=True)
class MaterializedInputSet:
    """Filesystem-backed immutable VASP inputs plus domain provenance entities."""

    calculation_id: CalculationId
    input_directory: str
    manifest: InputManifest
    artifacts: tuple[Artifact, ...]
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]
    manifest_artifact_id: ArtifactId = field(init=False)

    def __post_init__(self) -> None:
        _validate_relative_path(self.input_directory)
        if not self.artifacts:
            raise ValueError("MaterializedInputSet requires artifacts")
        artifact_ids = tuple(item.id for item in self.artifacts)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("materialized artifact ids must be unique")
        manifest_matches = tuple(
            item
            for item in self.artifacts
            if item.local_path is not None and item.local_path.endswith("/input-manifest.json")
        )
        if len(manifest_matches) != 1:
            raise ValueError("MaterializedInputSet requires exactly one input-manifest artifact")
        object.__setattr__(self, "manifest_artifact_id", manifest_matches[0].id)


def materialize_calculation_inputs(
    *,
    project_root: Path | str,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_incar: PreparedIncar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
) -> MaterializedInputSet:
    """Write immutable redistribution-safe VASP inputs and return Artifact/provenance records."""

    _validate_contracts(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        recipe=recipe,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
    )

    input_dir = Path("calculations") / str(calculation.id) / "inputs"
    generated: list[tuple[str, ArtifactType, str, str]] = [
        ("incar", ArtifactType.INCAR, "INCAR", prepared_incar.text),
        ("poscar", ArtifactType.POSCAR, "POSCAR", prepared_poscar.text),
        ("potcar_spec", ArtifactType.POTCAR_SPEC, "POTCAR.spec", potcar_spec.text),
    ]
    if prepared_kpoints.text is not None:
        generated.append(("kpoints", ArtifactType.KPOINTS, "KPOINTS", prepared_kpoints.text))

    atom_map_text = _atom_index_map_text(
        snapshot=snapshot,
        prepared_poscar=prepared_poscar,
    )
    generated.append(
        ("atom_index_map", ArtifactType.DERIVED_DATASET, "atom-index-map.json", atom_map_text)
    )
    generated.sort(key=lambda item: item[0])

    file_records = tuple(
        ManifestFileRecord(
            role=role,
            artifact_type=artifact_type,
            relative_path=(input_dir / filename).as_posix(),
            sha256=_sha256_text(text),
            size_bytes=len(text.encode("utf-8")),
        )
        for role, artifact_type, filename, text in generated
    )
    preparation_hash = canonical_sha256(
        {
            "calculation_hash": scientific_hash(calculation),
            "snapshot_hash": scientific_hash(snapshot),
            "fingerprint_instance_hash": fingerprint.instance_hash,
            "recipe_hash": recipe.recipe_hash,
            "system_kind": system_context.kind,
            "vacuum_axis": system_context.vacuum_axis,
            "prepared_poscar_sha256": prepared_poscar.sha256,
            "prepared_incar_sha256": prepared_incar.sha256,
            "prepared_kpoints_identity_hash": prepared_kpoints.identity_hash,
            "potcar_metadata_hash": potcar_spec.metadata_hash,
            "project_lock_hash": project_lock.lock_hash if project_lock is not None else None,
            "files": file_records,
        }
    )
    manifest_payload = _manifest_payload(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        recipe=recipe,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
        files=file_records,
        preparation_hash=preparation_hash,
    )
    manifest_text = _json_text(manifest_payload)
    manifest = InputManifest(
        calculation_id=calculation.id,
        files=file_records,
        payload=manifest_payload,
        text=manifest_text,
        sha256=_sha256_text(manifest_text),
        preparation_hash=preparation_hash,
    )

    root = Path(project_root)
    for (role, _artifact_type, filename, text), record in zip(
        generated, file_records, strict=True
    ):
        if role != record.role:
            raise InputMaterializationError("internal manifest/file ordering mismatch")
        _write_immutable_text(root / input_dir / filename, text)
    _write_immutable_text(root / input_dir / "input-manifest.json", manifest.text)

    producer = CalculationProducerRef(calculation.id)
    artifacts = [
        Artifact(
            artifact_type=record.artifact_type,
            producer=producer,
            availability=ArtifactAvailability.LOCAL,
            retrieval_policy=RetrievalPolicy.ALWAYS,
            local_path=record.relative_path,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
        )
        for record in file_records
    ]
    manifest_relative_path = (input_dir / "input-manifest.json").as_posix()
    manifest_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=producer,
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=manifest_relative_path,
        size_bytes=len(manifest.text.encode("utf-8")),
        sha256=manifest.sha256,
    )
    artifacts.append(manifest_artifact)
    artifact_tuple = tuple(artifacts)

    provenance_records = tuple(
        ProvenanceRecord(
            subject_id=artifact.id,
            tool=INPUT_GENERATOR_NAME,
            tool_version=INPUT_GENERATOR_VERSION,
            parameters_hash=manifest.preparation_hash,
            method_fingerprint_id=fingerprint.id,
        )
        for artifact in artifact_tuple
    )
    dependency_records = _dependency_records(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        input_artifacts=artifact_tuple[:-1],
        manifest_artifact=manifest_artifact,
    )
    return MaterializedInputSet(
        calculation_id=calculation.id,
        input_directory=input_dir.as_posix(),
        manifest=manifest,
        artifacts=artifact_tuple,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _validate_contracts(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_incar: PreparedIncar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
) -> None:
    if calculation.input_structure_snapshot_id != snapshot.id:
        raise InputMaterializationError(
            "Calculation input snapshot does not match StructureSnapshot"
        )
    if calculation.method_fingerprint_id != fingerprint.id:
        raise InputMaterializationError(
            "Calculation MethodFingerprint id does not match fingerprint"
        )
    if calculation.recipe_id != recipe.recipe_id:
        raise InputMaterializationError("Calculation recipe_id does not match RecipeIdentity")
    if fingerprint.recipe != recipe:
        raise InputMaterializationError("MethodFingerprint recipe does not match RecipeIdentity")
    if prepared_poscar.structure_snapshot_id != snapshot.id:
        raise InputMaterializationError("PreparedPoscar targets a different StructureSnapshot")
    if prepared_incar.structure_snapshot_id != snapshot.id:
        raise InputMaterializationError("PreparedIncar targets a different StructureSnapshot")
    if prepared_incar.recipe_id != recipe.recipe_id:
        raise InputMaterializationError("PreparedIncar recipe does not match RecipeIdentity")
    if prepared_kpoints.structure_snapshot_id != snapshot.id:
        raise InputMaterializationError("PreparedKPoints targets a different StructureSnapshot")
    if prepared_kpoints.system_context != system_context:
        raise InputMaterializationError("PreparedKPoints system context does not match")
    if potcar_spec.core_method_hash != fingerprint.core_method_hash:
        raise InputMaterializationError("POTCAR spec does not match fingerprint core method")
    if potcar_spec.species_order != prepared_poscar.species_order:
        raise InputMaterializationError("POTCAR species order does not match PreparedPoscar")
    if fingerprint.protocol.kpoints != prepared_kpoints.policy:
        raise InputMaterializationError("fingerprint Protocol k-point policy does not match plan")
    if fingerprint.protocol.encut_ev != _incar_float(prepared_incar, "ENCUT"):
        raise InputMaterializationError("fingerprint Protocol ENCUT does not match PreparedIncar")
    if project_lock is not None:
        if project_lock.project_id != calculation.project_id:
            raise InputMaterializationError(
                "Project numerical lock belongs to a different Project"
            )
        if project_lock.core_method_hash != fingerprint.core_method_hash:
            raise InputMaterializationError("Project numerical lock core method does not match")
        if project_lock.system_kind is not system_context.kind:
            raise InputMaterializationError("Project numerical lock system kind does not match")
        if project_lock.encut_ev != fingerprint.protocol.encut_ev:
            raise InputMaterializationError("Project numerical lock ENCUT does not match Protocol")
        if project_lock.kpoints != fingerprint.protocol.kpoints:
            raise InputMaterializationError("Project numerical lock k-points do not match Protocol")


def _incar_float(prepared_incar: PreparedIncar, name: str) -> float:
    for item in prepared_incar.parameters:
        if item.name == name:
            value = item.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InputMaterializationError(f"PreparedIncar {name} must be numeric")
            return float(value)
    raise InputMaterializationError(f"PreparedIncar is missing required {name}")


def _atom_index_map_text(
    *,
    snapshot: StructureSnapshot,
    prepared_poscar: PreparedPoscar,
) -> str:
    snapshot_hash = scientific_hash(snapshot)
    entries = []
    for entry in prepared_poscar.index_map.entries:
        flags = (
            None
            if prepared_poscar.selective_flags is None
            else list(prepared_poscar.selective_flags[entry.poscar_index])
        )
        entries.append(
            {
                "atom_uid": str(entry.atom_uid),
                "element": entry.element,
                "snapshot_index": entry.snapshot_index,
                "poscar_index": entry.poscar_index,
                "vasp_ordinal": entry.vasp_ordinal,
                "selective_dynamics": flags,
            }
        )
    payload: dict[str, object] = {
        "format": "ecatvasp-v03-atom-index-map",
        "version": 1,
        "structure_snapshot_id": str(snapshot.id),
        "structure_sha256": snapshot_hash,
        "poscar_sha256": prepared_poscar.sha256,
        "species_order": list(prepared_poscar.species_order),
        "species_counts": list(prepared_poscar.species_counts),
        "entries": entries,
    }
    return _json_text(payload)


def _manifest_payload(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_incar: PreparedIncar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
    files: tuple[ManifestFileRecord, ...],
    preparation_hash: str,
) -> dict[str, object]:
    effective_parameters = [
        {
            "name": item.name,
            "value": item.value,
            "source": item.source.value,
        }
        for item in prepared_incar.parameters
    ]
    file_payload = [
        {
            "role": item.role,
            "artifact_type": item.artifact_type.value,
            "relative_path": item.relative_path,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in files
    ]
    potcar_entries = [
        {
            "element": item.element,
            "symbol": item.symbol,
            "family": item.family,
            "titel": item.titel,
            "zval": item.zval,
            "enmax_ev": item.enmax_ev,
            "sha256": item.sha256,
        }
        for item in potcar_spec.entries
    ]
    return {
        "format": INPUT_MANIFEST_FORMAT,
        "version": INPUT_MANIFEST_VERSION,
        "calculation": {
            "id": str(calculation.id),
            "type": calculation.calculation_type.value,
            "engine": calculation.engine.value,
            "scientific_hash": scientific_hash(calculation),
        },
        "structure": {
            "snapshot_id": str(snapshot.id),
            "scientific_hash": scientific_hash(snapshot),
        },
        "method_fingerprint": {
            "id": str(fingerprint.id),
            "core_method_hash": fingerprint.core_method_hash,
            "protocol_hash": fingerprint.protocol_hash,
            "instance_hash": fingerprint.instance_hash,
        },
        "recipe": {
            "id": recipe.recipe_id,
            "version": recipe.version,
            "recipe_hash": recipe.recipe_hash,
        },
        "generator": {
            "name": INPUT_GENERATOR_NAME,
            "version": INPUT_GENERATOR_VERSION,
        },
        "system_context": {
            "kind": system_context.kind.value,
            "vacuum_axis": (
                system_context.vacuum_axis.value
                if system_context.vacuum_axis is not None
                else None
            ),
        },
        "preparations": {
            "poscar_sha256": prepared_poscar.sha256,
            "incar_sha256": prepared_incar.sha256,
            "kpoints_identity_hash": prepared_kpoints.identity_hash,
            "potcar_spec_sha256": potcar_spec.sha256,
            "potcar_metadata_hash": potcar_spec.metadata_hash,
            "atom_index_map_sha256": next(
                item.sha256 for item in files if item.role == "atom_index_map"
            ),
        },
        "effective_parameters": effective_parameters,
        "kpoints": {
            "policy_kind": prepared_kpoints.policy.kind.value,
            "mesh": list(prepared_kpoints.mesh),
            "centering": prepared_kpoints.centering.value,
            "uses_kpoints_file": prepared_kpoints.uses_kpoints_file,
            "kspacing_inv_angstrom": prepared_kpoints.kspacing_inv_angstrom,
            "kgamma": prepared_kpoints.kgamma,
        },
        "potcar_spec": {
            "species_order": list(potcar_spec.species_order),
            "entries": potcar_entries,
        },
        "validation": {
            "status": "passed",
            "project_lock_hash": project_lock.lock_hash if project_lock is not None else None,
        },
        "files": file_payload,
        "preparation_hash": preparation_hash,
    }


def _dependency_records(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    input_artifacts: tuple[Artifact, ...],
    manifest_artifact: Artifact,
) -> tuple[DependencyRecord, ...]:
    records: list[DependencyRecord] = []
    snapshot_hash = scientific_hash(snapshot)
    fingerprint_hash = scientific_hash(fingerprint)
    calculation_hash = scientific_hash(calculation)

    for artifact in input_artifacts:
        role = _artifact_role(artifact)
        records.extend(
            (
                DependencyRecord(
                    upstream_id=snapshot.id,
                    downstream_id=artifact.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"input_structure:{role}",
                    recorded_hash=snapshot_hash,
                ),
                DependencyRecord(
                    upstream_id=fingerprint.id,
                    downstream_id=artifact.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"method_fingerprint:{role}",
                    recorded_hash=fingerprint_hash,
                ),
                DependencyRecord(
                    upstream_id=calculation.id,
                    downstream_id=artifact.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"calculation_intent:{role}",
                    recorded_hash=calculation_hash,
                ),
            )
        )
        records.append(
            DependencyRecord(
                upstream_id=artifact.id,
                downstream_id=manifest_artifact.id,
                kind=DependencyKind.SCIENTIFIC,
                role=f"manifest_file:{role}",
                recorded_hash=scientific_hash(artifact),
            )
        )
    return tuple(records)


def _artifact_role(artifact: Artifact) -> str:
    if artifact.local_path is None:
        raise InputMaterializationError("materialized artifact is missing local_path")
    return Path(artifact.local_path).name.lower().replace(".", "_").replace("-", "_")


def _write_immutable_text(path: Path, text: str) -> None:
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file():
            raise InputMaterializationError(f"input path exists but is not a file: {path}")
        if path.read_bytes() != data:
            raise InputMaterializationError(
                f"immutable input already exists with different content: {path}"
            )
        return

    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise InputMaterializationError(
                    f"immutable input was concurrently created with different content: {path}"
                )
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_text(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_sha256(value: str, field_name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError("materialized paths must be non-blank project-relative paths")
