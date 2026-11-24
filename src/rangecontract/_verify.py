"""Evaluate scope and report the results of explicitly requested validation."""

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ._contract import Check, Contract, ContractError, parse_contract
from ._git import Change, GitError, Repository
from ._process import find_executable

EXIT_CODES = {"PASS": 0, "FAIL": 1, "ERROR": 2}


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    path: str | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    exit_code: int | None = None
    detail: str | None = None


@dataclass
class Report:
    status: str = "ERROR"
    base_commit: str | None = None
    target_commit: str | None = None
    contract_sha256: str | None = None
    scope: str = "NOT_EVALUATED"
    validation: str = "NOT_EVALUATED"
    changes: list[Change] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    schema_version: int = 1


def _scope_findings(changes: list[Change], contract: Contract) -> list[Finding]:
    findings = []
    count = len(changes)
    if count > contract.max_changed_files:
        findings.append(Finding(
            "changed_file_limit",
            f"{count} changed file records exceed the limit of {contract.max_changed_files}",
        ))
    paths = sorted({path for change in changes for path in change.paths})
    for path in paths:
        forbidden = next((glob for glob in contract.forbidden_paths if glob.matches(path)), None)
        if forbidden is not None:
            findings.append(Finding(
                "forbidden_path", "Path matches a forbidden pattern", path, forbidden.text,
            ))
        elif not any(glob.matches(path) for glob in contract.allowed_paths):
            findings.append(Finding("path_not_allowed", "Path matches no allowed pattern", path))
    return findings


def _skip(report: Report, checks: tuple[Check, ...], reason: str) -> None:
    report.checks.extend(CheckResult(check.name, "SKIPPED", detail=reason) for check in checks)


def _run_check(check: Check, root: Path) -> CheckResult:
    program = find_executable(check.argv[0], root)
    try:
        result = subprocess.run(
            [str(program), *check.argv[1:]],
            cwd=root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=check.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(check.name, "TIMEOUT", detail=f"exceeded {check.timeout_seconds} seconds")
    return CheckResult(check.name, "PASS" if result.returncode == 0 else "FAIL", result.returncode)


def _validate(
    report: Report,
    repo: Repository,
    target: str,
    contract: Contract,
    entered_path: Path,
    resolved_path: Path,
    run_checks: bool,
) -> None:
    if not contract.checks:
        report.validation = "NOT_REQUIRED"
        return
    report.validation = "INCOMPLETE"
    if report.scope != "PASS":
        _skip(report, contract.checks, "scope verification failed")
        return
    if not run_checks:
        _skip(report, contract.checks, "execution requires --run-checks")
        report.findings.append(Finding("checks_skipped", "Required validation was not executed"))
        return
    if any(path.is_relative_to(repo.root) for path in (entered_path, resolved_path)):
        _skip(report, contract.checks, "execution requires a trusted contract outside the checkout")
        raise GitError("execution requires a trusted contract outside the checkout")

    checkout = None
    for index, check in enumerate(contract.checks):
        result = None
        try:
            if checkout is None:
                checkout = repo.checkout_tree(target)
            repo.require_clean_checkout(target, checkout)
            result = _run_check(check, repo.root)
            repo.require_clean_checkout(target, checkout)
        except (OSError, ValueError, RuntimeError) as exc:
            detail = str(exc) if result is None else f"post-validation checkout: {exc}"
            exit_code = None if result is None else result.exit_code
            report.checks.append(CheckResult(check.name, "ERROR", exit_code, detail))
            _skip(report, contract.checks[index + 1:], "validation could not continue")
            report.validation = "ERROR"
            raise GitError("a required validation check could not complete") from exc
        report.checks.append(result)
    report.validation = (
        "PASS" if all(check.status == "PASS" for check in report.checks) else "FAIL"
    )


def verify(repo_path: Path, contract_path: Path, target: str, *, run_checks: bool) -> Report:
    report = Report()
    contract = None
    try:
        entered_path = Path(os.path.abspath(contract_path))
        resolved_path = contract_path.resolve(strict=True)
        contents = resolved_path.read_bytes()
        report.contract_sha256 = hashlib.sha256(contents).hexdigest()
        contract = parse_contract(contents)

        repo = Repository(repo_path)
        base = repo.resolve_commit(contract.base_commit)
        if base != contract.base_commit:
            raise ContractError("base_commit must identify the commit itself")
        report.base_commit = base
        report.target_commit = repo.resolve_commit(target)
        repo.require_ancestor(base, report.target_commit)
        report.changes = repo.changes(base, report.target_commit)

        report.findings = _scope_findings(report.changes, contract)
        report.scope = "FAIL" if report.findings else "PASS"
        _validate(report, repo, report.target_commit, contract, entered_path, resolved_path, run_checks)
        report.status = (
            "PASS" if report.scope == "PASS" and report.validation in {"PASS", "NOT_REQUIRED"}
            else "FAIL"
        )
    except (OSError, ValueError, RuntimeError) as exc:
        report.findings.append(Finding("verification_error", str(exc)))
        if contract is not None and contract.checks and not report.checks:
            _skip(report, contract.checks, "verification prerequisites failed")
            report.validation = "INCOMPLETE"
    return report
