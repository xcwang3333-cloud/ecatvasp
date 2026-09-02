"""Project persistence boundary for ECatVASP."""

from ecatvasp.storage.codec import StorageCodecError, dumps_storage, loads_storage
from ecatvasp.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    MigrationPathError,
    MigrationRegistry,
    UnsupportedSchemaVersionError,
)
from ecatvasp.storage.model import ProjectBundle, ProjectIntegrityError
from ecatvasp.storage.store import ProjectStorageError, ProjectStore

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "MigrationPathError",
    "MigrationRegistry",
    "ProjectBundle",
    "ProjectIntegrityError",
    "ProjectStorageError",
    "ProjectStore",
    "StorageCodecError",
    "UnsupportedSchemaVersionError",
    "dumps_storage",
    "loads_storage",
]
