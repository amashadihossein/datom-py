"""History and listing. R sources: ``datom_history()``, ``datom_list()``
(``R/query.R``), ``.datom_read_manifest()`` (``R/sync.R``) and
``.datom_manifest_upgrade()`` (``R/manifest-upgrade.R``).
"""

from __future__ import annotations

import fnmatch
from typing import Any

import pandas as pd

from ._checks import SUPPORTED_SCHEMA, check_schema_version, is_text_scalar, validate_name
from ._conn import Conn, check_conn
from ._errors import DatomError, InvalidArgument, ManifestUnreadable, NotFound
from ._metadata import metadata_key, read_json

HISTORY_COLUMNS = ["version", "data_sha", "timestamp", "author", "commit_message", "commit_sha"]
LIST_COLUMNS = ["name", "kind", "current_version", "current_data_sha", "last_updated"]
MANIFEST_KEY = ".metadata/manifest.json"


def _abbreviate(v: Any, n: int = 8) -> Any:
    return v[:n] if isinstance(v, str) else v


def history(conn: Conn, name: str, n: int = 10, short_hash: bool = False) -> pd.DataFrame:
    """A table's or set's versions, newest first.

    Columns: ``version``, ``data_sha``, ``timestamp``, ``author``,
    ``commit_message``, ``commit_sha``. Missing values are ``None``.
    """
    check_conn(conn)
    validate_name(name)
    if isinstance(n, bool) or not isinstance(n, (int, float)) or n < 1:
        raise InvalidArgument("`n` must be a positive integer.")

    try:
        entries = read_json(conn.store, metadata_key(name, "version_history"))
    except NotFound as e:
        raise NotFound(f"No version history found for {name!r}. It may not exist.") from e

    if not isinstance(entries, list) or not entries:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in HISTORY_COLUMNS})

    rows = []
    for entry in entries[: int(n)]:
        entry = entry if isinstance(entry, dict) else {}
        author = entry.get("author")
        if isinstance(author, dict):
            author = f"{author.get('name', '')} <{author.get('email', '')}>"
        commit_sha = entry.get("commit_sha")
        rows.append(
            {
                "version": entry.get("version"),
                "data_sha": entry.get("data_sha"),
                "timestamp": entry.get("timestamp"),
                "author": author,
                "commit_message": entry.get("commit_message"),
                "commit_sha": commit_sha if is_text_scalar(commit_sha) else None,
            }
        )
    if short_hash:
        for row in rows:
            for c in ("version", "data_sha", "commit_sha"):
                row[c] = _abbreviate(row[c])
    return pd.DataFrame(rows, columns=HISTORY_COLUMNS, dtype=object)


# --- manifest -------------------------------------------------------------------


def _upgrade_v1_to_v2(manifest: dict) -> dict:
    """Frozen once released, like its R counterpart: ``tables`` -> ``artifacts``,
    and an entry without ``kind`` is a table."""
    m = dict(manifest)
    if "tables" in m:
        m["artifacts"] = m.pop("tables")
    if isinstance(m.get("artifacts"), dict):
        m["artifacts"] = {
            k: ({"kind": "table", **e} if isinstance(e, dict) and e.get("kind") is None else e)
            for k, e in m["artifacts"].items()
        }
    return m


_UPGRADE_STEPS = {1: _upgrade_v1_to_v2}


def upgrade_manifest(manifest: Any, declared: int) -> Any:
    """Convert an older manifest to the current shape, in memory only."""
    if not isinstance(manifest, dict) or declared >= SUPPORTED_SCHEMA:
        return manifest
    for version in range(declared, SUPPORTED_SCHEMA):
        manifest = _UPGRADE_STEPS[version](manifest)
    manifest["schema_version"] = SUPPORTED_SCHEMA
    return manifest


def read_manifest(conn: Conn) -> dict:
    try:
        manifest = read_json(conn.store, MANIFEST_KEY)
    except DatomError as e:
        raise ManifestUnreadable(
            f"Could not read the manifest from storage. The repository may not be "
            f"initialized, or the manifest is missing. Underlying error: {e}"
        ) from e

    # A too-new manifest is refused. R datom would try to rebuild it from the
    # per-artifact documents first; that is out of scope here.
    declared = check_schema_version(manifest, MANIFEST_KEY)
    manifest = upgrade_manifest(manifest, declared)

    if not isinstance(manifest, dict) or "artifacts" not in manifest:
        raise ManifestUnreadable(
            "The manifest has no artifact list this package can use. R datom rebuilds the "
            "list from each artifact's metadata in this case; this package does not yet."
        )
    return manifest


def list_artifacts(
    conn: Conn, pattern: str | None = None, include_versions: bool = False, short_hash: bool = True
) -> pd.DataFrame:
    """The project's tables and sets, from its manifest.

    ``pattern`` is a glob on the name (``"ad*"``). R: ``datom_list()``, renamed
    so it doesn't shadow Python's ``list``.
    """
    check_conn(conn)
    manifest = read_manifest(conn)
    columns = LIST_COLUMNS + (["version_count"] if include_versions else [])

    artifacts = manifest.get("artifacts")
    names = list(artifacts) if isinstance(artifacts, dict) else []
    if pattern is not None:
        names = [nm for nm in names if fnmatch.fnmatchcase(nm, pattern)]

    rows = []
    for nm in names:
        entry = artifacts[nm] if isinstance(artifacts[nm], dict) else {}
        row = {
            "name": nm,
            "kind": entry.get("kind"),
            "current_version": entry.get("current_version"),
            "current_data_sha": entry.get("current_data_sha"),
            "last_updated": entry.get("last_updated"),
        }
        if include_versions:
            row["version_count"] = entry.get("version_count")
        if short_hash:
            row["current_version"] = _abbreviate(row["current_version"])
            row["current_data_sha"] = _abbreviate(row["current_data_sha"])
        rows.append(row)

    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in columns})
    return pd.DataFrame(rows, columns=columns)
