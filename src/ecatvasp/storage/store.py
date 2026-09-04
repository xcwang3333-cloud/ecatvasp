"""File-first project layout with SQLite metadata and integrity manifests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import cast
from uuid import UUID

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    Analysis,
    Artifact,
    Calculation,
    Catalyst,
    ExecutionAttempt,
    MethodFingerprint,
    Project,
    RemoteJob,
    ScientificWorkflowPlan,
    StateConformer,
    StructureSnapshot,
    StructureVariant,
    WorkflowStepBinding,
)
from ecatvasp.domain.calculation import (
    AnalysisProducerRef,
    CalculationProducerRef,
    ExecutionAttemptProducerRef,
)
from ecatvasp.storage.codec import dumps_storage, loads_storage
from ecatvasp.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    MigrationRegistry,
    UnsupportedSchemaVersionError,
)
from ecatvasp.storage.model import ProjectBundle, ProjectIntegrityError

PROJECT_FILENAME = "project.yaml"
DATABASE_RELATIVE_PATH = Path(".workbench") / "project.sqlite"
PROJECT_MANIFEST_RELATIVE_PATH = Path(".workbench") / "manifests" / "project.json"
ARTIFACT_MANIFEST_RELATIVE_DIR = Path(".workbench") / "manifests" / "artifacts"

_PROJECT_DIRS = (
    "structures",
    "calculations",
    "analyses",
    "references",
    "reactions",
    "figures",
    "exports",
)


class ProjectStorageError(RuntimeError):
    """Raised when a project store cannot be read or written safely."""


class ProjectStore:
    """Persist and reopen one self-contained ECatVASP project directory."""

    def __init__(self, root: Path | str, *, migrations: MigrationRegistry | None = None) -> None:
        self.root = Path(root)
        self.migrations = migrations if migrations is not None else MigrationRegistry()

    @property
    def database_path(self) -> Path:
        return self.root / DATABASE_RELATIVE_PATH

    @property
    def project_manifest_path(self) -> Path:
        return self.root / PROJECT_MANIFEST_RELATIVE_PATH

    def save(self, bundle: ProjectBundle) -> None:
        """Atomically replace metadata after validating the complete project graph."""

        bundle.validate()
        if bundle.project.schema_version != CURRENT_SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                "Project.schema_version must match the current storage schema"
            )
        self._prepare_layout()

        temporary_database = self.database_path.with_suffix(".sqlite.tmp")
        if temporary_database.exists():
            temporary_database.unlink()
        try:
            connection = sqlite3.connect(temporary_database)
            try:
                self._initialize_database(connection)
                self._write_bundle(connection, bundle)
                connection.commit()
            finally:
                connection.close()
            os.replace(temporary_database, self.database_path)
        except Exception:
            if temporary_database.exists():
                temporary_database.unlink()
            raise

        entity_manifest = self._entity_manifest(bundle)
        self._write_project_yaml(bundle.project)
        self._write_artifact_manifests(bundle.artifacts)
        manifest = {
            "format": "ecatvasp-project-manifest",
            "schema_version": CURRENT_SCHEMA_VERSION,
            "project_id": str(bundle.project.id),
            "database": DATABASE_RELATIVE_PATH.as_posix(),
            "database_sha256": _sha256_file(self.database_path),
            "entities": entity_manifest,
        }
        _write_text_atomic(
            self.project_manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    def open(self) -> ProjectBundle:
        """Verify integrity, migrate when explicitly supported, and rebuild the domain graph."""

        if not self.database_path.is_file() or not self.project_manifest_path.is_file():
            raise ProjectStorageError("project database or manifest is missing")
        manifest = self._read_manifest()
        expected_database_hash = manifest.get("database_sha256")
        if not isinstance(expected_database_hash, str):
            raise ProjectStorageError("project manifest is missing database_sha256")
        if _sha256_file(self.database_path) != expected_database_hash:
            raise ProjectStorageError("project.sqlite does not match its integrity manifest")

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            schema_version = self._schema_version(connection)
            if schema_version > CURRENT_SCHEMA_VERSION:
                raise UnsupportedSchemaVersionError(
                    f"project schema {schema_version} is newer than supported schema "
                    f"{CURRENT_SCHEMA_VERSION}"
                )
            if schema_version < CURRENT_SCHEMA_VERSION:
                with connection:
                    self.migrations.migrate(
                        connection,
                        from_version=schema_version,
                        to_version=CURRENT_SCHEMA_VERSION,
                    )
            bundle, row_manifest = self._read_bundle(connection)
        finally:
            connection.close()

        if schema_version < CURRENT_SCHEMA_VERSION:
            self.save(bundle)
            return bundle

        self._validate_manifest(manifest, bundle, row_manifest)
        return bundle

    def _prepare_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in _PROJECT_DIRS:
            (self.root / directory).mkdir(exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    def _initialize_database(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                project_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
            );
            CREATE TABLE relations (
                source_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                target_id TEXT NOT NULL,
                PRIMARY KEY (source_id, relation, target_id),
                FOREIGN KEY (source_id) REFERENCES entities(entity_id),
                FOREIGN KEY (target_id) REFERENCES entities(entity_id)
            );
            CREATE INDEX idx_entities_type ON entities(entity_type);
            CREATE INDEX idx_relations_target ON relations(target_id);
            """
        )

    def _write_bundle(self, connection: sqlite3.Connection, bundle: ProjectBundle) -> None:
        metadata = (
            ("format", "ecatvasp-project"),
            ("schema_version", str(CURRENT_SCHEMA_VERSION)),
            ("project_id", str(bundle.project.id)),
        )
        connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata)

        for ordinal, entity in enumerate(bundle.entities()):
            entity_id = _entity_uuid(entity)
            payload = dumps_storage(entity)
            digest = _sha256_text(payload)
            connection.execute(
                """
                INSERT INTO entities(
                    entity_id, entity_type, project_id, ordinal, payload_json, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(entity_id),
                    type(entity).__name__,
                    str(bundle.project.id),
                    ordinal,
                    payload,
                    digest,
                ),
            )

        for entity in bundle.entities():
            source_id = str(_entity_uuid(entity))
            for relation, target_id in _entity_relations(entity):
                connection.execute(
                    "INSERT INTO relations(source_id, relation, target_id) VALUES (?, ?, ?)",
                    (source_id, relation, str(target_id)),
                )

    def _read_bundle(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[ProjectBundle, list[dict[str, object]]]:
        rows = connection.execute(
            """
            SELECT entity_id, entity_type, ordinal, payload_json, content_sha256
            FROM entities ORDER BY ordinal
            """
        ).fetchall()
        entities: list[object] = []
        row_manifest: list[dict[str, object]] = []
        for entity_id, entity_type, ordinal, payload, expected_hash in rows:
            if not isinstance(entity_id, str) or not isinstance(entity_type, str):
                raise ProjectStorageError("invalid entity row identity fields")
            if not isinstance(ordinal, int) or not isinstance(payload, str):
                raise ProjectStorageError("invalid entity row payload fields")
            if not isinstance(expected_hash, str):
                raise ProjectStorageError("invalid entity row hash")
            if _sha256_text(payload) != expected_hash:
                raise ProjectStorageError(f"entity payload hash mismatch: {entity_id}")
            entity = loads_storage(payload)
            if type(entity).__name__ != entity_type:
                raise ProjectStorageError(f"entity type mismatch: {entity_id}")
            if str(_entity_uuid(entity)) != entity_id:
                raise ProjectStorageError(f"entity identity mismatch: {entity_id}")
            entities.append(entity)
            row_manifest.append(
                {
                    "id": entity_id,
                    "type": entity_type,
                    "ordinal": ordinal,
                    "sha256": expected_hash,
                }
            )
        try:
            return ProjectBundle.from_entities(tuple(entities)), row_manifest
        except ProjectIntegrityError as error:
            raise ProjectStorageError("persisted project graph is inconsistent") from error

    def _schema_version(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or not isinstance(row[0], str):
            raise ProjectStorageError("project database has no schema_version")
        try:
            return int(row[0])
        except ValueError as error:
            raise ProjectStorageError("invalid project schema_version") from error

    def _entity_manifest(self, bundle: ProjectBundle) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for ordinal, entity in enumerate(bundle.entities()):
            payload = dumps_storage(entity)
            records.append(
                {
                    "id": str(_entity_uuid(entity)),
                    "type": type(entity).__name__,
                    "ordinal": ordinal,
                    "sha256": _sha256_text(payload),
                }
            )
        return records

    def _read_manifest(self) -> dict[str, object]:
        try:
            raw: object = json.loads(self.project_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectStorageError("project manifest cannot be read") from error
        if not isinstance(raw, dict):
            raise ProjectStorageError("project manifest must be a JSON object")
        return cast(dict[str, object], raw)

    def _validate_manifest(
        self,
        manifest: dict[str, object],
        bundle: ProjectBundle,
        row_manifest: list[dict[str, object]],
    ) -> None:
        if manifest.get("schema_version") != CURRENT_SCHEMA_VERSION:
            raise ProjectStorageError("manifest schema_version does not match current schema")
        if manifest.get("project_id") != str(bundle.project.id):
            raise ProjectStorageError("manifest project_id does not match the Project")
        if manifest.get("entities") != row_manifest:
            raise ProjectStorageError("manifest entity index does not match project.sqlite")

    def _write_project_yaml(self, project: Project) -> None:
        text = "\n".join(
            (
                f"schema_version: {CURRENT_SCHEMA_VERSION}",
                f"project_id: {json.dumps(str(project.id))}",
                f"name: {json.dumps(project.name, ensure_ascii=False)}",
                f"slug: {json.dumps(project.slug)}",
                f"database: {json.dumps(DATABASE_RELATIVE_PATH.as_posix())}",
                "",
            )
        )
        _write_text_atomic(self.root / PROJECT_FILENAME, text)

    def _write_artifact_manifests(self, artifacts: tuple[Artifact, ...]) -> None:
        directory = self.root / ARTIFACT_MANIFEST_RELATIVE_DIR
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for artifact in artifacts:
            payload = {
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type.value,
                "producer_kind": artifact.producer.kind.value,
                "producer_id": str(artifact.producer.id),
                "availability": artifact.availability.value,
                "retrieval_policy": artifact.retrieval_policy.value,
                "local_path": artifact.local_path,
                "remote_path": artifact.remote_path,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }
            _write_text_atomic(
                directory / f"{artifact.id}.json",
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )


def _entity_uuid(entity: object) -> UUID:
    value = getattr(entity, "id", None)
    if not isinstance(value, UUID):
        raise ProjectStorageError("persisted entity has no UUID id")
    return value


def _entity_relations(entity: object) -> tuple[tuple[str, UUID], ...]:
    relations: list[tuple[str, UUID]] = []
    if isinstance(entity, Catalyst):
        relations.append(("project", entity.project_id))
    elif isinstance(entity, StructureVariant):
        relations.append(("catalyst", entity.catalyst_id))
        if entity.parent_variant_id is not None:
            relations.append(("parent_variant", entity.parent_variant_id))
        if entity.current_structure_snapshot_id is not None:
            relations.append(("current_snapshot", entity.current_structure_snapshot_id))
    elif isinstance(entity, StructureSnapshot):
        if entity.parent_snapshot_id is not None:
            relations.append(("parent_snapshot", entity.parent_snapshot_id))
    elif isinstance(entity, ActiveSite):
        relations.append(("structure_variant", entity.structure_variant_id))
    elif isinstance(entity, AdsorptionState):
        relations.append(("structure_variant", entity.structure_variant_id))
        if entity.active_site_id is not None:
            relations.append(("active_site", entity.active_site_id))
    elif isinstance(entity, StateConformer):
        relations.extend(
            (
                ("adsorption_state", entity.adsorption_state_id),
                ("structure_snapshot", entity.structure_snapshot_id),
            )
        )
        if entity.parent_conformer_id is not None:
            relations.append(("parent_conformer", entity.parent_conformer_id))
    elif isinstance(entity, ScientificWorkflowPlan):
        relations.extend(
            (
                ("project", entity.project_id),
                ("root_snapshot", entity.root_structure_snapshot_id),
            )
        )
    elif isinstance(entity, Calculation):
        relations.extend(
            (
                ("project", entity.project_id),
                ("input_snapshot", entity.input_structure_snapshot_id),
                ("method_fingerprint", entity.method_fingerprint_id),
            )
        )
    elif isinstance(entity, WorkflowStepBinding):
        relations.extend(
            (
                ("workflow_plan", entity.workflow_plan_id),
                ("calculation", entity.calculation_id),
                ("resolved_input_snapshot", entity.resolved_input_structure_snapshot_id),
            )
        )
        if entity.supersedes_binding_id is not None:
            relations.append(("supersedes_binding", entity.supersedes_binding_id))
    elif isinstance(entity, ExecutionAttempt):
        relations.append(("calculation", entity.calculation_id))
        if entity.previous_attempt_id is not None:
            relations.append(("previous_attempt", entity.previous_attempt_id))
    elif isinstance(entity, RemoteJob):
        relations.append(("execution_attempt", entity.execution_attempt_id))
    elif isinstance(entity, Artifact):
        producer = entity.producer
        if isinstance(producer, CalculationProducerRef):
            relations.append(("producer_calculation", producer.id))
        elif isinstance(producer, ExecutionAttemptProducerRef):
            relations.append(("producer_attempt", producer.id))
        elif isinstance(producer, AnalysisProducerRef):
            relations.append(("producer_analysis", producer.id))
    elif isinstance(entity, Analysis):
        relations.append(("project", entity.project_id))
        relations.extend(("input_artifact", item) for item in entity.input_artifact_ids)
    elif isinstance(entity, MethodFingerprint | Project):
        pass
    return tuple(relations)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
