"""PyInstaller entry point for the ECatVASP desktop stdio backend."""

from ecatvasp.desktop.host import main


if __name__ == "__main__":
    raise SystemExit(main())
