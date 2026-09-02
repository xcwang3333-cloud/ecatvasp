from __future__ import annotations

from hashlib import sha256

import pytest

from ecatvasp.domain.ids import new_uuid7
from ecatvasp.provenance import (
    DependencyGraph,
    DependencyKind,
    DependencyRecord,
    FreshnessEngine,
    FreshnessState,
    ProvenanceIntegrityError,
)


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def test_execution_dependency_change_does_not_stale_scientific_result() -> None:
    execution_profile_id = new_uuid7()
    calculation_id = new_uuid7()
    dependency = DependencyRecord(
        upstream_id=execution_profile_id,
        downstream_id=calculation_id,
        kind=DependencyKind.EXECUTION,
        role="execution_profile",
        recorded_hash=_digest("ncore-4"),
    )

    result = FreshnessEngine((dependency,)).evaluate(
        node_ids={execution_profile_id, calculation_id},
        current_hashes={execution_profile_id: _digest("ncore-8")},
    )

    assert result[calculation_id].state is FreshnessState.FRESH


def test_scientific_change_stales_only_scientific_downstream_chain() -> None:
    snapshot_id = new_uuid7()
    calculation_id = new_uuid7()
    artifact_id = new_uuid7()
    analysis_id = new_uuid7()
    unrelated_id = new_uuid7()
    dependencies = (
        DependencyRecord(
            upstream_id=snapshot_id,
            downstream_id=calculation_id,
            kind=DependencyKind.SCIENTIFIC,
            role="input_structure",
            recorded_hash=_digest("snapshot-v1"),
        ),
        DependencyRecord(
            upstream_id=calculation_id,
            downstream_id=artifact_id,
            kind=DependencyKind.SCIENTIFIC,
            role="producer_calculation",
            recorded_hash=_digest("calculation-v1"),
        ),
        DependencyRecord(
            upstream_id=artifact_id,
            downstream_id=analysis_id,
            kind=DependencyKind.SCIENTIFIC,
            role="input_artifact",
            recorded_hash=_digest("artifact-v1"),
        ),
    )

    result = FreshnessEngine(dependencies).evaluate(
        node_ids={snapshot_id, calculation_id, artifact_id, analysis_id, unrelated_id},
        current_hashes={
            snapshot_id: _digest("snapshot-v2"),
            calculation_id: _digest("calculation-v1"),
            artifact_id: _digest("artifact-v1"),
        },
    )

    assert result[snapshot_id].state is FreshnessState.FRESH
    assert result[calculation_id].state is FreshnessState.STALE
    assert result[artifact_id].state is FreshnessState.STALE
    assert result[analysis_id].state is FreshnessState.STALE
    assert result[unrelated_id].state is FreshnessState.FRESH


def test_missing_scientific_hash_fails_closed_as_invalid() -> None:
    upstream_id = new_uuid7()
    downstream_id = new_uuid7()
    dependency = DependencyRecord(
        upstream_id=upstream_id,
        downstream_id=downstream_id,
        kind=DependencyKind.SCIENTIFIC,
        role="required_input",
        recorded_hash=_digest("recorded"),
    )

    result = FreshnessEngine((dependency,)).evaluate(
        node_ids={upstream_id, downstream_id},
        current_hashes={},
    )

    assert result[downstream_id].state is FreshnessState.INVALID
    assert {reason.code for reason in result[downstream_id].reasons} == {
        "scientific_hash_missing"
    }


def test_invalid_scientific_upstream_propagates_invalidity() -> None:
    upstream_id = new_uuid7()
    middle_id = new_uuid7()
    downstream_id = new_uuid7()
    dependencies = (
        DependencyRecord(
            upstream_id=upstream_id,
            downstream_id=middle_id,
            kind=DependencyKind.SCIENTIFIC,
            role="first",
            recorded_hash=_digest("upstream"),
        ),
        DependencyRecord(
            upstream_id=middle_id,
            downstream_id=downstream_id,
            kind=DependencyKind.SCIENTIFIC,
            role="second",
            recorded_hash=_digest("middle"),
        ),
    )

    result = FreshnessEngine(dependencies).evaluate(
        node_ids={upstream_id, middle_id, downstream_id},
        current_hashes={
            upstream_id: _digest("upstream"),
            middle_id: _digest("middle"),
        },
        invalid_ids={middle_id},
    )

    assert result[middle_id].state is FreshnessState.INVALID
    assert result[downstream_id].state is FreshnessState.INVALID


def test_superseded_is_scientifically_valid_and_does_not_propagate() -> None:
    upstream_id = new_uuid7()
    downstream_id = new_uuid7()
    dependency = DependencyRecord(
        upstream_id=upstream_id,
        downstream_id=downstream_id,
        kind=DependencyKind.SCIENTIFIC,
        role="input",
        recorded_hash=_digest("same"),
    )

    result = FreshnessEngine((dependency,)).evaluate(
        node_ids={upstream_id, downstream_id},
        current_hashes={upstream_id: _digest("same")},
        superseded_ids={upstream_id},
    )

    assert result[upstream_id].state is FreshnessState.SUPERSEDED
    assert result[downstream_id].state is FreshnessState.FRESH


def test_stale_takes_precedence_over_superseded() -> None:
    upstream_id = new_uuid7()
    downstream_id = new_uuid7()
    dependency = DependencyRecord(
        upstream_id=upstream_id,
        downstream_id=downstream_id,
        kind=DependencyKind.SCIENTIFIC,
        role="input",
        recorded_hash=_digest("old"),
    )

    result = FreshnessEngine((dependency,)).evaluate(
        node_ids={upstream_id, downstream_id},
        current_hashes={upstream_id: _digest("new")},
        superseded_ids={downstream_id},
    )

    assert result[downstream_id].state is FreshnessState.STALE


def test_dependency_graph_rejects_cycles_and_duplicate_semantics() -> None:
    first = new_uuid7()
    second = new_uuid7()
    forward = DependencyRecord(
        upstream_id=first,
        downstream_id=second,
        kind=DependencyKind.SCIENTIFIC,
        role="edge",
        recorded_hash=_digest("a"),
    )
    backward = DependencyRecord(
        upstream_id=second,
        downstream_id=first,
        kind=DependencyKind.SCIENTIFIC,
        role="edge",
        recorded_hash=_digest("b"),
    )
    with pytest.raises(ProvenanceIntegrityError, match="DAG"):
        DependencyGraph((forward, backward))

    duplicate = DependencyRecord(
        upstream_id=first,
        downstream_id=second,
        kind=DependencyKind.SCIENTIFIC,
        role="edge",
        recorded_hash=_digest("c"),
    )
    with pytest.raises(ProvenanceIntegrityError, match="duplicate dependency semantics"):
        DependencyGraph((forward, duplicate))
