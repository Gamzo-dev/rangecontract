"""Resolve programs before launching them from a checkout."""

import os
from pathlib import Path


def find_executable(program: str, checkout: Path) -> Path:
    path = Path(program)
    windows = os.name == "nt"
    if path.is_absolute():
        candidates = (path,)
    else:
        if path.name != program or path.anchor:
            raise ValueError("executable must be an absolute path or a name on PATH")
        filename = program + ".exe" if windows and not path.suffix else program
        candidates = (
            Path(directory) / filename
            for directory in os.get_exec_path()
            if Path(directory).is_absolute()
        )

    for candidate in candidates:
        # Windows can dispatch .bat and .cmd via a shell even with shell=False.
        if windows and candidate.suffix.lower() != ".exe":
            continue
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        resolved = candidate.resolve()
        if windows and resolved.suffix.lower() != ".exe":
            continue
        if path.is_absolute() or not (
            candidate.is_relative_to(checkout) or resolved.is_relative_to(checkout)
        ):
            return candidate
    raise FileNotFoundError(f"executable {program!r} was not found in the permitted locations")
