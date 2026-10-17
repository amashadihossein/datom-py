"""Read-only storage backends.

A store maps a *relative key* (``dm/.metadata/metadata.json``) to bytes under
``{prefix}/datom/``. It can only read: there is no write, delete or create
method, by design.

Only the local-folder store exists so far. An S3 store will implement the same
methods.
"""

from __future__ import annotations

from pathlib import Path

from ._errors import InvalidArgument, InvalidAddress, NotFound, StoreUnreachable


def build_storage_key(prefix: str | None, key: str) -> str:
    """``{prefix}/datom/{key}``, as R's ``.datom_build_storage_key()`` builds it."""
    parts = [prefix.strip("/") if prefix is not None else "", "datom", key.strip("/")]
    return "/".join(p for p in parts if p)


class LocalStore:
    """A datom store in a local folder. Use :func:`datom.store_local` to make one."""

    backend = "local"

    def __init__(self, path: str | Path, prefix: str | None = None) -> None:
        if not isinstance(path, (str, Path)) or str(path) == "":
            raise InvalidArgument("`path` must be a non-empty string or path.")
        if prefix is not None and not isinstance(prefix, str):
            raise InvalidArgument("`prefix` must be a string or None.")
        self.root = Path(path).expanduser().absolute()
        self.prefix = prefix

    def __repr__(self) -> str:
        return f"LocalStore(root={str(self.root)!r}, prefix={self.prefix!r})"

    def check_reachable(self) -> None:
        # R datom creates a missing folder here. A reader never writes, so it
        # refuses instead.
        if not self.root.is_dir():
            raise StoreUnreachable(f"Data store directory {str(self.root)!r} does not exist.")

    def _path(self, key: str) -> Path:
        full = self.root.joinpath(*build_storage_key(self.prefix, key).split("/"))
        # Callers only splice validated names and addresses into keys; this is a
        # second line of defence against a key that escapes the namespace.
        base = self.root.joinpath(*build_storage_key(self.prefix, "").split("/")).resolve()
        resolved = full.resolve()
        if resolved != base and base not in resolved.parents:
            raise InvalidAddress(f"Storage key {key!r} escapes the datom namespace.")
        return full

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise NotFound(f"Not found in local store {str(self.root)!r}: {key!r}")
        return path.read_bytes()


def store_local(path: str | Path, prefix: str | None = None) -> LocalStore:
    """A read-only store over a local folder that R datom wrote to.

    ``path`` is the folder passed to R's ``datom_store_local()``; the data lives
    under ``{path}/{prefix}/datom/``.
    """
    return LocalStore(path, prefix)
