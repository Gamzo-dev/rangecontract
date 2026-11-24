"""Read exact Git objects and preflight opted-in validation."""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ._contract import OBJECT_ID
from ._process import find_executable

_GIT_TIMEOUT = 60
_PAIRED_STATUS = re.compile(rb"[RC]([0-9]{1,3})")


class GitError(RuntimeError):
    """Git could not establish the requested range or checkout state."""


@dataclass(frozen=True)
class Change:
    status: str
    paths: tuple[str, ...]
    similarity: int | None = None


def parse_name_status(data: bytes) -> list[Change]:
    if not data:
        return []
    fields = data.split(b"\0")
    if fields.pop() != b"":
        raise GitError("unterminated Git name-status output")

    changes = []
    index = 0
    while index < len(fields):
        status = fields[index]
        score = None
        if status in {b"A", b"M", b"D"}:
            count = 1
        else:
            match = _PAIRED_STATUS.fullmatch(status)
            if match is None or (score := int(match[1])) > 100:
                raise GitError(f"unsupported Git change status {status!r}")
            count = 2

        raw_paths = fields[index + 1:index + 1 + count]
        if len(raw_paths) != count:
            raise GitError("incomplete Git name-status record")
        try:
            paths = tuple(path.decode("utf-8") for path in raw_paths)
        except UnicodeDecodeError as exc:
            raise GitError("Git change paths must be valid UTF-8") from exc
        if any(part in {"", ".", ".."} for path in paths for part in path.split("/")):
            raise GitError("invalid repository-relative path in Git name-status output")
        if count == 2 and paths[0] == paths[1]:
            raise GitError("Git rename or copy has identical source and destination")

        changes.append(Change(status[:1].decode("ascii"), paths, score))
        index += count + 1
    return sorted(changes, key=lambda change: (change.paths, change.status))


class Repository:
    def __init__(self, path: Path) -> None:
        self.root = path.resolve(strict=True)
        self.program = find_executable("git", self.root)
        self.env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        self.env.update(
            LC_ALL="C",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_ATTR_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_ALLOW_PROTOCOL="",
            GIT_TERMINAL_PROMPT="0",
        )

        bare = self._run("rev-parse", "--is-bare-repository").stdout
        if bare not in {b"true\n", b"false\n"}:
            raise GitError("Git did not report a repository type")
        self.bare = bare == b"true\n"
        root_arg = "--absolute-git-dir" if self.bare else "--show-toplevel"
        if self._git_path("rev-parse", root_arg).resolve() != self.root:
            raise GitError("--repo must identify a Git repository root")

    def _run(
        self,
        *args: str,
        accepted: tuple[int, ...] = (0,),
        index: Path | None = None,
        options: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[bytes]:
        env = self.env if index is None else {**self.env, "GIT_INDEX_FILE": str(index)}
        try:
            result = subprocess.run(
                [
                    str(self.program),
                    "--no-pager",
                    "--no-replace-objects",
                    "-c", "core.fsmonitor=false",
                    "-c", "core.untrackedCache=false",
                    "-C", str(self.root),
                    *options,
                    *args,
                ],
                env=env,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_GIT_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} exceeded {_GIT_TIMEOUT} seconds") from exc
        if result.returncode not in accepted:
            detail = result.stderr[:500].decode("utf-8", errors="replace").strip()
            raise GitError(f"git {args[0]} exited {result.returncode}: {detail or 'no diagnostic'}")
        return result

    def _git_path(self, *args: str) -> Path:
        output = self._run(*args).stdout
        if not output.endswith(b"\n") or len(output) == 1:
            raise GitError("Git did not report a repository path")
        return Path(os.fsdecode(output[:-1]))

    def resolve_commit(self, revision: str) -> str:
        if not revision or "\0" in revision:
            raise GitError("a nonempty Git revision is required")
        output = self._run(
            "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
        ).stdout
        oid = output.removesuffix(b"\n").decode("ascii", errors="replace")
        if OBJECT_ID.fullmatch(oid) is None:
            raise GitError("Git did not resolve a full commit ID")
        return oid

    def _has_config(self, pattern: str) -> bool:
        return self._run(
            "config", "--name-only", "--get-regexp", pattern, accepted=(0, 1),
        ).returncode == 0

    def _has_git_path(self, relative: str) -> bool:
        path = self._git_path("rev-parse", "--path-format=absolute", "--git-path", relative)
        return path.exists() or path.is_symlink()

    def require_ancestor(self, base: str, target: str) -> None:
        shallow = self._run("rev-parse", "--is-shallow-repository").stdout
        if shallow != b"false\n":
            raise GitError("a complete repository is required; shallow history is unsupported")
        if self._has_git_path("info/grafts"):
            raise GitError("repositories with Git grafts are unsupported")
        ancestry = self._run("merge-base", "--is-ancestor", base, target, accepted=(0, 1))
        if ancestry.returncode == 1:
            raise GitError("the pinned base commit is not an ancestor of the target")

    def changes(self, base: str, target: str) -> list[Change]:
        if self._has_git_path("info/attributes") or self._has_config(r"^diff\..*\.binary$"):
            raise GitError("repository-local diff attribute overrides are unsupported")

        # Keep Git's similarity detection on committed blobs and target attributes.
        with tempfile.TemporaryDirectory(prefix="rangecontract-diff-") as temporary:
            return parse_name_status(self._run(
                "diff-tree",
                "--no-commit-id", "--no-color", "--name-status", "-r", "-z",
                "--no-relative", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none",
                "-O/dev/null", "--rename-empty", "-M50%", "-C50%", "--find-copies-harder", "-l0",
                base, target, "--",
                index=Path(temporary) / "index",
                options=(
                    f"--attr-source={target}",
                    "-c", "core.attributesFile=/dev/null",
                    "-c", "core.bigFileThreshold=512m",
                ),
            ).stdout)

    def require_clean_checkout(self, target: str) -> None:
        if self.bare:
            raise GitError("validation requires a working tree")
        if self.resolve_commit("HEAD") != target:
            raise GitError("validation checkout HEAD does not match the target commit")
        if self._has_config(r"^filter\..*\.(clean|process)$"):
            raise GitError("validation does not support configured Git clean or process filters")
        staged = self._run(
            "diff-index", "--cached", "--quiet", "--no-ext-diff", "--ignore-submodules=none",
            target, "--", accepted=(0, 1),
        )
        if staged.returncode == 1:
            raise GitError("validation checkout index differs from the target commit")

        # A fresh index forces Git to inspect the working tree independently
        # of cached stat data and flags such as assume-unchanged.
        with tempfile.TemporaryDirectory(prefix="rangecontract-index-") as temporary:
            index = Path(temporary) / "index"
            options = ("-c", "core.splitIndex=false", "-c", "core.sparseCheckout=false")
            self._run("read-tree", target, index=index, options=options)
            entries = self._run("ls-files", "--stage", "-z", index=index).stdout
            if any(entry.startswith(b"160000 ") for entry in entries.split(b"\0")):
                raise GitError("validation does not support checkouts with submodules")
            status = self._run(
                "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching",
                "--ignore-submodules=none", index=index, options=options,
            )
            if status.stdout:
                raise GitError("validation checkout has modified, untracked, or ignored files")
