"""Verify a materialized checkout against a Git tree without Git filters."""

import hashlib
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ._contract import OBJECT_ID

_DEVICE_NAMES = {
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"{prefix}{digit}" for prefix in ("com", "lpt") for digit in "123456789¹²³"),
}
_CHUNK_SIZE = 1024 * 1024


class CheckoutError(RuntimeError):
    """The target cannot be represented by the validation checkout."""


@dataclass(frozen=True)
class Blob:
    oid: str
    size: int
    executable: bool


def _component_key(name: str) -> str:
    # Unicode D145 canonical caseless matching.
    decomposed = unicodedata.normalize("NFD", name)
    return unicodedata.normalize("NFD", decomposed.casefold())


def _portable_component(name: str) -> bool:
    return (
        bool(name)
        and name[-1] not in ". "
        and _component_key(name) != ".git"
        and name.partition(".")[0].rstrip(" ").casefold() not in _DEVICE_NAMES
        and not any(ord(char) < 32 or char in '<>:"\\|?*' for char in name)
    )


def _link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


@dataclass(frozen=True)
class Checkout:
    entries: dict[str, Blob | None]
    algorithm: str

    @classmethod
    def from_tree(cls, data: bytes, target: str) -> "Checkout":
        if data and not data.endswith(b"\0"):
            raise CheckoutError("unterminated Git tree inventory")
        entries: dict[str, Blob | None] = {}
        aliases: dict[tuple[str, str], str] = {}
        for record in data.split(b"\0")[:-1]:
            metadata, separator, raw_path = record.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 4:
                raise CheckoutError("invalid Git tree inventory record")
            mode, object_type, raw_oid, size = fields
            oid = raw_oid.decode("ascii", errors="replace")
            if len(oid) != len(target) or OBJECT_ID.fullmatch(oid) is None:
                raise CheckoutError("invalid object ID in Git tree inventory")

            try:
                path = raw_path.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CheckoutError("validation tree paths must be valid UTF-8") from exc
            if not all(_portable_component(part) for part in path.split("/")):
                raise CheckoutError(f"unsupported validation tree path {path!r}")
            if path in entries:
                raise CheckoutError(f"duplicate validation tree path {path!r}")
            parent, _, name = path.rpartition("/")
            alias = (parent, _component_key(name))
            if alias in aliases:
                raise CheckoutError(f"validation tree path collision: {aliases[alias]!r} and {path!r}")
            aliases[alias] = path

            if (mode, object_type, size) == (b"040000", b"tree", b"-"):
                entries[path] = None
            elif mode in {b"100644", b"100755"} and object_type == b"blob" and size.isdigit():
                entries[path] = Blob(oid, int(size), mode == b"100755")
            else:
                raise CheckoutError(f"unsupported Git tree entry {mode!r} at {path!r}")

        for path in entries:
            parent = path.rpartition("/")[0]
            if parent and (parent not in entries or entries[parent] is not None):
                raise CheckoutError(f"invalid parent directory for validation tree path {path!r}")

        return cls(entries, "sha1" if len(target) == 40 else "sha256")

    def require_match(self, root: Path) -> None:
        root_info = root.lstat()
        if _link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise CheckoutError("validation checkout root must be a plain directory")

        remaining = self.entries.copy()
        pending = [("", root)]
        admin_found = False
        while pending:
            prefix, directory = pending.pop()
            with os.scandir(directory) as listing:
                for entry in sorted(listing, key=lambda item: item.name):
                    relative = prefix + entry.name
                    info = entry.stat(follow_symlinks=False)
                    if _link_or_reparse(info):
                        raise CheckoutError(f"link or reparse point in validation checkout at {relative!r}")
                    if relative == ".git":
                        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                            raise CheckoutError("invalid root Git administrative entry")
                        admin_found = True
                    elif relative not in remaining:
                        raise CheckoutError(f"unexpected validation checkout entry {relative!r}")
                    elif (blob := remaining.pop(relative)) is None:
                        if not stat.S_ISDIR(info.st_mode):
                            raise CheckoutError(f"expected a directory in validation checkout at {relative!r}")
                        pending.append((relative + "/", Path(entry.path)))
                    else:
                        self._require_blob(entry.path, relative, info, blob)

        if not admin_found:
            raise CheckoutError("validation checkout is missing its root Git administrative entry")
        if remaining:
            raise CheckoutError(f"missing validation checkout entry {min(remaining)!r}")

    def _require_blob(self, path: str, relative: str, info: os.stat_result, blob: Blob) -> None:
        if not stat.S_ISREG(info.st_mode):
            raise CheckoutError(f"expected a regular file in validation checkout at {relative!r}")
        if os.name != "nt" and bool(info.st_mode & stat.S_IXUSR) != blob.executable:
            raise CheckoutError(f"validation checkout executable bit differs at {relative!r}")

        different = f"validation checkout raw content differs from the target blob at {relative!r}"
        if info.st_size != blob.size:
            raise CheckoutError(different)
        digest = hashlib.new(self.algorithm, f"blob {blob.size}\0".encode("ascii"))
        unread = blob.size
        with open(path, "rb") as file:
            while unread:
                chunk = file.read(min(unread, _CHUNK_SIZE))
                if not chunk:
                    raise CheckoutError(different)
                digest.update(chunk)
                unread -= len(chunk)
            if file.read(1) or digest.hexdigest() != blob.oid:
                raise CheckoutError(different)
