from __future__ import annotations

import json
from pathlib import Path

from ecatvasp.desktop import DesktopBackend, DesktopOperation, DesktopRequest


def test_desktop_shell_health_fixture_is_owned_by_python_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture_path = (
        root
        / "ui"
        / "desktop"
        / "src"
        / "lib"
        / "backend"
        / "fixtures"
        / "health-response.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    response = DesktopBackend().handle(
        DesktopRequest(
            request_id="desktop-shell-health-fixture",
            operation=DesktopOperation.HEALTH,
        )
    )

    assert fixture == response.to_dict()
