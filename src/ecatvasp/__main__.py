"""Allow ``python -m ecatvasp`` to invoke the headless CLI."""

from ecatvasp.cli import main

raise SystemExit(main())
