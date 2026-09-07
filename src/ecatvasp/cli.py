"""Standard-library headless command-line interface for ECatVASP."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from ecatvasp.api.application import ApplicationReportFormat, ApplicationServiceError
from ecatvasp.api.facade import (
    open_project,
    render_headless_json,
    render_inspection_text,
    render_status_text,
)
from ecatvasp.reporting import ScientificReportError
from ecatvasp.storage import (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)

_RUNTIME_ERRORS = (
    ApplicationServiceError,
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    ScientificReportError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable Block 7 headless CLI parser."""

    parser = argparse.ArgumentParser(
        prog="ecatvasp",
        description="ECatVASP headless project inspection and reporting",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.9.0.dev0")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser("inspect", help="inspect one persisted project")
    inspect_parser.add_argument("project_root", type=Path)
    inspect_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )

    status_parser = commands.add_parser("status", help="show separated project lifecycle status")
    status_parser.add_argument("project_root", type=Path)
    status_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )

    report_parser = commands.add_parser("report", help="render deterministic scientific report")
    report_parser.add_argument("project_root", type=Path)
    report_parser.add_argument(
        "--format",
        choices=tuple(item.value for item in ApplicationReportFormat),
        required=True,
        dest="output_format",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Execute one headless command; runtime failures return 1 without project mutation."""

    parser = build_parser()
    args = parser.parse_args(None if argv is None else list(argv))
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    try:
        facade = open_project(args.project_root)
        if args.command == "inspect":
            document = facade.inspect()
            content = (
                render_headless_json(document)
                if args.output_format == "json"
                else render_inspection_text(document)
            )
        elif args.command == "status":
            document = facade.status()
            content = (
                render_headless_json(document)
                if args.output_format == "json"
                else render_status_text(document)
            )
        elif args.command == "report":
            content = facade.report(format=args.output_format).content
        else:
            parser.error("unsupported command")
            return 2
    except _RUNTIME_ERRORS as error:
        err.write(f"ecatvasp: {error}\n")
        return 1

    out.write(content)
    return 0
