"""Module entry point for the ECatVASP desktop backend sidecar."""

from __future__ import annotations

from ecatvasp.desktop.host import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
