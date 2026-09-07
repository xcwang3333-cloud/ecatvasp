"""Desktop read-model composition over current authoritative scientific state."""

from __future__ import annotations

from ecatvasp.frontend import FrontendHandoff, build_frontend_handoff
from ecatvasp.storage import ProjectBundle
from ecatvasp.visualization import build_structure_presentation


def build_desktop_frontend_handoff(bundle: ProjectBundle) -> FrontendHandoff:
    """Build the current desktop handoff without persisting or inferring scientific state.

    Structure presentations are deterministic projections of every current StructureSnapshot and are
    rebuilt for each reopened ProjectBundle. Workflow readiness and result-specific presentations are
    intentionally not synthesized here because their exact scientific evidence/selection belongs to
    existing caller-supplied authorities.
    """

    presentations = tuple(
        build_structure_presentation(snapshot)
        for snapshot in bundle.structure_snapshots
    )
    return build_frontend_handoff(bundle, presentations=presentations)
