"""Versioned frontend handoff transport over existing v0.9 read/presentation authorities."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256

from ecatvasp import __version__
from ecatvasp.reporting import ScientificPresentation, ScientificReport, build_scientific_report
from ecatvasp.storage import ProjectBundle
from ecatvasp.visualization import PRESENTATION_CONTRACT_VERSION
from ecatvasp.visualization.matterviz import (
    MATTERVIZ_CONTRACT_VERSION,
    MATTERVIZ_TARGET_VERSION,
)
from ecatvasp.workspace import WorkflowReadinessDashboard, WorkspaceScientificInventory

FRONTEND_HANDOFF_CONTRACT_VERSION = "ecatvasp-frontend-handoff-v1"


class FrontendHandoffError(ValueError):
    """Raised when a frontend handoff cannot preserve the current backend contracts exactly."""


@dataclass(frozen=True, slots=True)
class FrontendCapability:
    """One explicit transport capability offered to a frontend client."""

    name: str
    contract_version: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.contract_version.strip():
            raise FrontendHandoffError("frontend capabilities require non-empty names and versions")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "contract_version": self.contract_version}


@dataclass(frozen=True, slots=True)
class FrontendHandoff:
    """Deterministic read-only transport envelope for desktop/web presentation clients."""

    report: ScientificReport
    capabilities: tuple[FrontendCapability, ...]
    backend_version: str = __version__
    contract_version: str = FRONTEND_HANDOFF_CONTRACT_VERSION
    handoff_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_version != FRONTEND_HANDOFF_CONTRACT_VERSION:
            raise FrontendHandoffError("unsupported frontend handoff contract version")
        names = tuple(item.name for item in self.capabilities)
        if len(names) != len(set(names)):
            raise FrontendHandoffError("frontend capability names must be unique")
        payload = _handoff_payload(self, include_hash=False)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        object.__setattr__(self, "handoff_hash", sha256(canonical.encode("utf-8")).hexdigest())

    def to_dict(self) -> dict[str, object]:
        """Return the complete deterministic JSON-compatible handoff payload."""

        return _handoff_payload(self, include_hash=True)


def build_frontend_handoff(
    bundle: ProjectBundle,
    *,
    inventory: WorkspaceScientificInventory | None = None,
    readiness: tuple[WorkflowReadinessDashboard, ...] = (),
    presentations: tuple[ScientificPresentation, ...] = (),
) -> FrontendHandoff:
    """Compose a transport envelope through the existing reporting validation boundary."""

    report = build_scientific_report(
        bundle,
        inventory=inventory,
        readiness=readiness,
        presentations=presentations,
    )
    capabilities = (
        FrontendCapability("workspace-report", report.contract_version),
        FrontendCapability("scientific-presentation", PRESENTATION_CONTRACT_VERSION),
        FrontendCapability("matterviz", MATTERVIZ_CONTRACT_VERSION),
    )
    return FrontendHandoff(report=report, capabilities=capabilities)


def render_frontend_handoff_json(handoff: FrontendHandoff) -> str:
    """Serialize the full transport envelope deterministically for a frontend boundary."""

    return json.dumps(
        handoff.to_dict(),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def _handoff_payload(
    handoff: FrontendHandoff,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": handoff.contract_version,
        "backend_version": handoff.backend_version,
        "capabilities": [item.to_dict() for item in handoff.capabilities],
        "matterviz_target_version": MATTERVIZ_TARGET_VERSION,
        "report": handoff.report.to_dict(),
    }
    if include_hash:
        payload["handoff_hash"] = handoff.handoff_hash
    return payload
