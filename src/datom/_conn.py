"""Reader connections. R source: ``.datom_get_conn_reader()`` (``R/conn.R``)."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from ._checks import is_text_scalar
from ._errors import GovernanceAttachedWarning, InvalidArgument
from ._metadata import read_json

GOVERNANCE_KEY = ".metadata/governance.json"


@dataclass(frozen=True)
class Conn:
    """A read-only connection to one project's data store.

    It holds the store and the project name, and never a secret.
    """

    store: Any
    project_name: str

    def __repr__(self) -> str:
        return f"Conn(project_name={self.project_name!r}, store={self.store!r})"


def _governance_marker(store: Any) -> dict | None:
    """The project's governance marker if storage holds a valid one, else None.

    Any failure to read or validate it means "no marker", as in R: the probe only
    decides whether to warn.
    """
    try:
        if not store.exists(GOVERNANCE_KEY):
            return None
        content = read_json(store, GOVERNANCE_KEY)
    except Exception:
        return None
    if not isinstance(content, dict) or not is_text_scalar(content.get("gov_repo_url")):
        return None
    gs = content.get("gov_storage")
    if not isinstance(gs, dict) or gs.get("type") not in ("s3", "local") or not is_text_scalar(gs.get("root")):
        return None
    return content


def get_conn(store: Any, project_name: str) -> Conn:
    """Open a reader connection: storage access only, no git, no writes.

    If the project's storage holds a governance marker, warns that the location
    you supplied may go stale after a data move, then carries on with it, as R
    datom does. Finding a project's location through governance is not
    supported yet.
    """
    if not is_text_scalar(project_name):
        raise InvalidArgument("`project_name` is required for reader connections.")
    if not callable(getattr(store, "read_bytes", None)) or not callable(getattr(store, "exists", None)):
        raise InvalidArgument("`store` must be a store, e.g. from datom.store_local().")

    marker = _governance_marker(store)
    if marker is not None:
        gs = marker["gov_storage"]
        warnings.warn(
            GovernanceAttachedWarning(
                f"Project {project_name!r} has governance attached, but you connected with "
                "data-store credentials only. The connection uses the location you supplied, "
                "which may go stale after a data migration. "
                f"Gov repo: {marker['gov_repo_url']}. Gov storage: {gs['type']} / {gs['root']}."
            ),
            stacklevel=2,
        )

    store.check_reachable()
    return Conn(store=store, project_name=project_name)


def check_conn(conn: Any) -> Conn:
    if not isinstance(conn, Conn):
        raise InvalidArgument("`conn` must be a connection from datom.get_conn().")
    return conn
