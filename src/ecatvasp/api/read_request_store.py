"""Request-scoped ProjectStore reuse for read-only desktop operations.

The wrapper performs one normal, fully verified ``ProjectStore.open()`` and reuses that
immutable ``ProjectBundle`` only for the lifetime of one read request. It is deliberately
read-only: mutation paths must continue to use ``ProjectStore`` directly so source-change
reopens and post-save verification remain authoritative.
"""

from __future__ import annotations

from pathlib import Path

from ecatvasp.storage import ProjectBundle, ProjectStorageError, ProjectStore


class RequestScopedVerifiedReadStore(ProjectStore):
    """Memoize one fully verified bundle for one read-only application request."""

    def __init__(self, root: Path | str) -> None:
        super().__init__(root)
        self._verified_bundle: ProjectBundle | None = None

    def open(self) -> ProjectBundle:
        if self._verified_bundle is None:
            self._verified_bundle = super().open()
        return self._verified_bundle

    def save(self, bundle: ProjectBundle) -> None:
        del bundle
        raise ProjectStorageError(
            "request-scoped verified read store cannot persist project mutations"
        )


__all__ = ["RequestScopedVerifiedReadStore"]
