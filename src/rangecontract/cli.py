"""Command-line entry point and report rendering."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from ._git import Repository
from ._verify import EXIT_CODES, Report, verify


def _quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _human(report: Report) -> str:
    lines = [
        report.status,
        f"Base commit:      {report.base_commit or '-'}",
        f"Target commit:    {report.target_commit or '-'}",
        f"Contract SHA-256: {report.contract_sha256 or '-'}",
        f"Scope:            {report.scope}",
        f"Validation:       {report.validation}",
    ]
    if report.scope != "NOT_EVALUATED":
        lines.append(f"Changed files:    {len(report.changes)}")
    for change in report.changes:
        lines.append(f"  {change.status} " + " -> ".join(_quoted(path) for path in change.paths))
    for finding in report.findings:
        path = f" {_quoted(finding.path)}" if finding.path is not None else ""
        pattern = f" ({_quoted(finding.pattern)})" if finding.pattern is not None else ""
        lines.append(f"  [{finding.code}]{path}: {_quoted(finding.message)}{pattern}")
    for check in report.checks:
        code = f" (exit {check.exit_code})" if check.exit_code is not None else ""
        detail = f": {_quoted(check.detail)}" if check.detail is not None else ""
        lines.append(f"  Check {_quoted(check.name)}: {check.status}{code}{detail}")
    return "\n".join(lines)


def _init(repo_path: Path, base: str, path: Path) -> int:
    try:
        commit = Repository(repo_path).resolve_commit(base)
        with path.open("x", encoding="utf-8", newline="\n") as file:
            file.write(
                "# Add allowed paths and set the change limit before use.\n"
                f'base_commit = "{commit}"\n'
                "allowed_paths = []\n"
                "forbidden_paths = []\n"
                "max_changed_files = 0\n"
            )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {_quoted(str(exc))}", file=sys.stderr)
        return 2
    print(f"Created {_quoted(str(path))} with base {commit}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rangecontract", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="write a deny-by-default contract template")
    initialize.add_argument("--repo", type=Path, default=Path("."), help="repository root")
    initialize.add_argument("--base", default="HEAD", help="base revision to pin (default: HEAD)")
    initialize.add_argument("--contract", type=Path, default=Path(".rangecontract.toml"),
                            help="output file (default: .rangecontract.toml)")

    check = commands.add_parser("verify", help="verify the pinned base to target commit range")
    check.add_argument("--repo", type=Path, default=Path("."), help="repository root")
    check.add_argument("--contract", type=Path, required=True, help="trusted TOML contract")
    check.add_argument("--target", default="HEAD", help="target revision (default: HEAD)")
    check.add_argument("--run-checks", action="store_true", help="execute required validation")
    check.add_argument("--json", action="store_true", help="write a JSON report")

    commands.add_parser("version", help="print the installed version")

    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"rangecontract {__version__}")
        return 0
    if args.command == "init":
        return _init(args.repo, args.base, args.contract)
    report = verify(args.repo, args.contract, args.target, run_checks=args.run_checks)
    print(json.dumps(asdict(report), indent=2) if args.json else _human(report))
    return EXIT_CODES[report.status]
