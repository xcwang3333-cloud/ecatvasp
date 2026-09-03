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


def test_schema_v2_has_builtin_v1_migration() -> None:
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
    legacy = Project(name="Legacy", slug="legacy", schema_version=1)
    payload = dumps_storage(legacy)
    connection.execute("INSERT INTO metadata(key, value) VALUES ('schema_version', '1')")
    connection.execute(
        "INSERT INTO entities(entity_id, entity_type, payload_json, content_sha256) "
        "VALUES (?, 'Project', ?, ?)",
        (
            str(legacy.id),
            payload,
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        ),
    )

    registry = MigrationRegistry()
    assert CURRENT_SCHEMA_VERSION == 2
    assert registry.plan(1, 2) == (1,)
    registry.migrate(connection, from_version=1, to_version=2)

    schema_version = connection.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()
    migrated_payload, migrated_hash = connection.execute(
        "SELECT payload_json, content_sha256 FROM entities WHERE entity_type = 'Project'"
    ).fetchone()
    restored = loads_storage(migrated_payload)

    assert schema_version == ("2",)
    assert isinstance(restored, Project)
    assert restored.schema_version == 2
    assert migrated_hash == hashlib.sha256(migrated_payload.encode("utf-8")).hexdigest()


def test_migration_registry_still_rejects_missing_future_step() -> None:
    registry = MigrationRegistry()

    with pytest.raises(MigrationPathError):
        registry.plan(2, 3)
