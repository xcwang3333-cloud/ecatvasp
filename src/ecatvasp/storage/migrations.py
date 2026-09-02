"""Schema-version and migration boundary for ECatVASP project stores."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

CURRENT_SCHEMA_VERSION = 1
MigrationStep = Callable[[sqlite3.Connection], None]


class UnsupportedSchemaVersionError(ValueError):
    """Raised when a project schema cannot be opened by this build."""


class MigrationPathError(ValueError):
    """Raised when no complete migration path exists."""


@dataclass(slots=True)
class MigrationRegistry:
    """Registry for explicit one-version-at-a-time SQLite migrations."""

    _steps: dict[int, MigrationStep] = field(default_factory=dict)

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
