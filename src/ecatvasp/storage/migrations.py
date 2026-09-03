"""Schema-version and migration boundary for ECatVASP project stores."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from ecatvasp.schema.version import SCHEMA_VERSION

CURRENT_SCHEMA_VERSION = SCHEMA_VERSION
MigrationStep = Callable[[sqlite3.Connection], None]


class UnsupportedSchemaVersionError(ValueError):
    """Raised when a project schema cannot be opened by this build."""


class MigrationPathError(ValueError):
    """Raised when no complete migration path exists."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Advance persisted Project metadata while preserving legacy execution payloads.

    Schema v2 adds optional execution-provenance fields. Legacy ExecutionAttempt rows can decode
    without rewriting because the new fields have conservative defaults, but the persisted Project
    entity itself must advertise schema version 2 before ProjectStore re-materializes the bundle.
    """

    rows = connection.execute(
        "SELECT entity_id, payload_json FROM entities WHERE entity_type = 'Project'"
    ).fetchall()
    if len(rows) != 1:
        raise MigrationPathError("schema v1 project store must contain exactly one Project entity")

    entity_id, payload_json = rows[0]
    if not isinstance(entity_id, str) or not isinstance(payload_json, str):
        raise MigrationPathError("schema v1 Project row is malformed")
    try:
        raw = json.loads(payload_json)
    except json.JSONDecodeError as error:
        raise MigrationPathError("schema v1 Project payload is not valid JSON") from error
    if not isinstance(raw, dict):
        raise MigrationPathError("schema v1 Project payload must be a mapping")

    payload = cast(dict[str, object], raw)
    if payload.get("$ecatvasp") != "dataclass" or payload.get("class") != "Project":
        raise MigrationPathError("schema v1 Project payload has an unexpected storage tag")
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        raise MigrationPathError("schema v1 Project payload is missing dataclass fields")
    fields = cast(dict[str, object], raw_fields)
    fields["schema_version"] = 2

    migrated_payload = _canonical_json(payload)
    migrated_hash = hashlib.sha256(migrated_payload.encode("utf-8")).hexdigest()
    connection.execute(
        "UPDATE entities SET payload_json = ?, content_sha256 = ? WHERE entity_id = ?",
        (migrated_payload, migrated_hash, entity_id),
    )


@dataclass(slots=True)
class MigrationRegistry:
    """Registry for explicit one-version-at-a-time SQLite migrations."""

    _steps: dict[int, MigrationStep] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if CURRENT_SCHEMA_VERSION >= 2 and 1 not in self._steps:
            self._steps[1] = _migrate_v1_to_v2

    def register(self, from_version: int, step: MigrationStep) -> None:
        """Register the migration ``from_version -> from_version + 1``."""

        if from_version < 1:
            raise ValueError("from_version must be positive")
        if from_version in self._steps:
            raise ValueError(f"migration from schema {from_version} is already registered")
        self._steps[from_version] = step

    def plan(self, from_version: int, to_version: int) -> tuple[int, ...]:
        """Return starting versions for the required consecutive migration steps."""

        if from_version < 1 or to_version < 1:
            raise ValueError("schema versions must be positive")
        if from_version > to_version:
            raise UnsupportedSchemaVersionError(
                f"project schema {from_version} is newer than supported schema {to_version}"
            )
        versions = tuple(range(from_version, to_version))
        missing = tuple(version for version in versions if version not in self._steps)
        if missing:
            raise MigrationPathError(f"missing migration steps from schema versions: {missing}")
        return versions

    def migrate(
        self,
        connection: sqlite3.Connection,
        *,
        from_version: int,
        to_version: int = CURRENT_SCHEMA_VERSION,
    ) -> None:
        """Apply an explicit migration path inside the caller's transaction."""

        for version in self.plan(from_version, to_version):
            self._steps[version](connection)
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
                (str(version + 1),),
            )
