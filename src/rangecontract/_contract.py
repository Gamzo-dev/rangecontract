"""Parse the contract and match portable, repository-relative Git paths."""

import re
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase

OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600


class ContractError(ValueError):
    """A contract cannot be evaluated according to this schema."""


@dataclass(frozen=True)
class Glob:
    text: str
    components: tuple[str | None, ...]

    @classmethod
    def parse(cls, pattern: str) -> "Glob":
        components = []
        for component in pattern.split("/"):
            if component == "**":
                components.append(None)
                continue
            if component in {"", ".", ".."}:
                raise ContractError(f"invalid path pattern {pattern!r}")

            translated = []
            index = 0
            while index < len(component):
                char = component[index]
                if char == "\\":
                    index += 1
                    if index == len(component) or component[index] not in r"\*?":
                        raise ContractError(f"invalid escape in path pattern {pattern!r}")
                    char = component[index]
                    translated.append(f"[{char}]" if char in "*?" else char)
                elif char == "*" and component[index + 1:index + 2] == "*":
                    raise ContractError(f"'**' must be a whole component in {pattern!r}")
                else:
                    translated.append("[[]" if char == "[" else char)
                index += 1
            components.append("".join(translated))
        return cls(pattern, tuple(components))

    def matches(self, path: str) -> bool:
        parts = path.split("/")
        reachable = {0}
        for component in self.components:
            if component is None:
                reachable = set(range(min(reachable), len(parts) + 1))
            else:
                reachable = {
                    index + 1
                    for index in reachable
                    if index < len(parts) and fnmatchcase(parts[index], component)
                }
                if not reachable:
                    return False
        return len(parts) in reachable


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class Contract:
    base_commit: str
    allowed_paths: tuple[Glob, ...]
    forbidden_paths: tuple[Glob, ...]
    max_changed_files: int
    checks: tuple[Check, ...]


def _fields(table: dict, label: str, *, required: set[str], optional: set[str]) -> None:
    missing = required - table.keys()
    unknown = table.keys() - required - optional
    if missing:
        raise ContractError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def _string(value: object) -> bool:
    return isinstance(value, str) and bool(value) and "\0" not in value


def _patterns(value: object, label: str) -> tuple[Glob, ...]:
    if not isinstance(value, list) or not all(_string(item) for item in value):
        raise ContractError(f"{label} must be an array of nonempty path patterns")
    return tuple(Glob.parse(item) for item in value)


def _checks(value: object) -> tuple[Check, ...]:
    if not isinstance(value, list):
        raise ContractError("checks must be an array of tables")

    checks = []
    names = set()
    for index, table in enumerate(value, start=1):
        label = f"checks[{index}]"
        if not isinstance(table, dict):
            raise ContractError(f"{label} must be a table")
        _fields(table, label, required={"name", "argv"}, optional={"timeout_seconds"})
        name = table["name"]
        argv = table["argv"]
        timeout = table.get("timeout_seconds", DEFAULT_TIMEOUT)
        if not _string(name) or not name.strip():
            raise ContractError(f"{label}.name must be a non-blank string")
        if name in names:
            raise ContractError(f"duplicate check name {name!r}")
        if (
            not isinstance(argv, list)
            or not argv
            or not _string(argv[0])
            or not argv[0].strip()
            or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
        ):
            raise ContractError(f"{label}.argv must be a nonempty array of strings with an executable")
        if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT:
            raise ContractError(f"{label}.timeout_seconds must be an integer from 1 to {MAX_TIMEOUT}")
        names.add(name)
        checks.append(Check(name, tuple(argv), timeout))
    return tuple(checks)


def parse_contract(contents: bytes) -> Contract:
    try:
        table = tomllib.loads(contents.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContractError(f"invalid UTF-8 TOML: {exc}") from exc

    _fields(
        table,
        "contract",
        required={"base_commit", "allowed_paths", "forbidden_paths", "max_changed_files"},
        optional={"checks"},
    )
    base = table["base_commit"]
    if not isinstance(base, str) or OBJECT_ID.fullmatch(base) is None:
        raise ContractError("base_commit must be a full lowercase Git commit ID")
    maximum = table["max_changed_files"]
    if type(maximum) is not int or maximum < 0:
        raise ContractError("max_changed_files must be a nonnegative integer")
    return Contract(
        base,
        _patterns(table["allowed_paths"], "allowed_paths"),
        _patterns(table["forbidden_paths"], "forbidden_paths"),
        maximum,
        _checks(table.get("checks", [])),
    )
