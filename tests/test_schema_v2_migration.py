from __future__ import annotations

import hashlib
import sqlite3

import pytest

from ecatvasp.domain import Project
from ecatvasp.storage import (
    CURRENT_SCHEMA_VERSION,
    MigrationPathError,
    MigrationRegistry,
    dumps_storage,
    loads_storage,
)


def _legacy_connection(project: Project) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE entities (
            entity_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            content_sha256 TEXT NOT NULL
        );
        """
    )
    payload = dumps_storage(project)
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
        (str(project.schema_version),),
    )
    connection.execute(
        "INSERT INTO entities(entity_id, entity_type, payload_json, content_sha256) "
        "VALUES (?, 'Project', ?, ?)",
        (
            str(project.id),
            payload,
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        ),
    )
    return connection


def _assert_project_schema(connection: sqlite3.Connection, expected: int) -> None:
    schema_version = connection.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()
    migrated_payload, migrated_hash = connection.execute(
        "SELECT payload_json, content_sha256 FROM entities WHERE entity_type = 'Project'"
    ).fetchone()
    restored = loads_storage(migrated_payload)

    assert schema_version == (str(expected),)
    assert isinstance(restored, Project)
    assert restored.schema_version == expected
    assert migrated_hash == hashlib.sha256(migrated_payload.encode("utf-8")).hexdigest()


def test_schema_v3_has_builtin_v1_through_v3_migration() -> None:
    connection = _legacy_connection(Project(name="Legacy", slug="legacy", schema_version=1))

    registry = MigrationRegistry()
    assert CURRENT_SCHEMA_VERSION == 3
    assert registry.plan(1, 3) == (1, 2)
    registry.migrate(connection, from_version=1, to_version=3)

    _assert_project_schema(connection, 3)


def test_schema_v3_migrates_v2_without_rewriting_non_project_rows() -> None:
    connection = _legacy_connection(Project(name="V2", slug="v2", schema_version=2))
    payload = '{"legacy":"scientific-row"}'
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    connection.execute(
        "INSERT INTO entities(entity_id, entity_type, payload_json, content_sha256) "
        "VALUES ('legacy-calculation', 'Calculation', ?, ?)",
        (payload, content_hash),
    )

    registry = MigrationRegistry()
    assert registry.plan(2, 3) == (2,)
    registry.migrate(connection, from_version=2, to_version=3)

    _assert_project_schema(connection, 3)
    preserved = connection.execute(
        "SELECT payload_json, content_sha256 FROM entities WHERE entity_id = 'legacy-calculation'"
    ).fetchone()
    assert preserved == (payload, content_hash)


def test_migration_registry_rejects_missing_future_step() -> None:
    registry = MigrationRegistry()

    with pytest.raises(MigrationPathError):
        registry.plan(3, 4)
