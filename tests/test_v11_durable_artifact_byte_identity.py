from __future__ import annotations

import hashlib
from pathlib import Path

from ecatvasp.analysis.dos_materialization import _write_canonical_artifact
from ecatvasp.domain import Analysis, AnalysisStatus, AnalysisType, Project, canonical_json


def test_canonical_dos_writer_persists_exact_hashed_bytes(tmp_path: Path) -> None:
    project = Project(name="Byte Identity", slug="byte-identity")
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.DOS,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
    )
    payload = {
        "format": "byte-identity-regression",
        "version": 1,
        "message": "canonical durable artifact",
    }

    artifact = _write_canonical_artifact(
        root=tmp_path,
        analysis=analysis,
        payload=payload,
    )

    expected = (canonical_json(payload) + "\n").encode("utf-8")
    assert artifact.local_path is not None
    observed = (tmp_path / artifact.local_path).read_bytes()

    assert observed == expected
    assert observed.endswith(b"\n")
    assert b"\r\n" not in observed
    assert artifact.size_bytes == len(observed)
    assert artifact.sha256 == hashlib.sha256(observed).hexdigest()

    reopened = _write_canonical_artifact(
        root=tmp_path,
        analysis=analysis,
        payload=payload,
    )
    assert reopened.size_bytes == artifact.size_bytes
    assert reopened.sha256 == artifact.sha256
