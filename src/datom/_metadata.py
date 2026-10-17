"""Reading an artifact's metadata and resolving a version to a data address.

R sources: ``.datom_read_metadata()`` and ``.datom_resolve_version()``
(``R/read_write.R``), ``.datom_recorded_current_version()``
(``R/manifest-rebuild.R``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ._checks import check_schema_version, is_text_scalar, validate_name
from ._errors import (
    DocumentUnreadable,
    InvalidArgument,
    MetadataCorrupt,
    VersionAmbiguous,
    VersionNotFound,
)


def read_json(store: Any, key: str) -> Any:
    raw = store.read_bytes(key)
    try:
        return json.loads(raw)
    except ValueError as e:
        raise DocumentUnreadable(f"Failed to parse JSON at {key!r}: {e}") from e


def metadata_key(name: str, which: str) -> str:
    return f"{validate_name(name)}/.metadata/{which}.json"


def read_metadata(store: Any, name: str) -> tuple[Any, Any]:
    """``metadata.json`` (format-checked) and ``version_history.json`` for one artifact."""
    key = metadata_key(name, "metadata")
    current = read_json(store, key)
    # Checked before the second read: this is the only place the data path sees
    # a format number.
    check_schema_version(current, key)
    history = read_json(store, metadata_key(name, "version_history"))
    return current, history


@dataclass(frozen=True)
class Resolved:
    """Where a version's content lives, and how to verify it."""

    data_sha: str
    #: The recorded hash of the stored object's bytes (``parquet_sha`` or
    #: ``document_sha``). ``None`` or ``""`` when absent; the caller decides what
    #: that means.
    object_sha: Any
    #: The version as recorded in the history, never recomputed. ``None`` when
    #: the history records nothing matching the current document.
    version: str | None


def _recorded(doc: Any, field: str) -> Any:
    return doc.get(field) if isinstance(doc, dict) else None


def recorded_current_version(meta: Any, history: Any) -> str | None:
    """The history's own version string for the current metadata document.

    Not always the newest entry: a write that reverts to content already in the
    history appends nothing. So candidates are narrowed by ``data_sha`` first and
    by ``created_at`` (the entry's ``timestamp``) second; the first survivor wins.
    """
    if not isinstance(history, list) or not history:
        return None
    data_sha = _recorded(meta, "data_sha")
    candidates = [
        e for e in history if isinstance(e, dict) and data_sha is not None and e.get("data_sha") == data_sha
    ]
    if len(candidates) > 1:
        created_at = _recorded(meta, "created_at")
        exact = [e for e in candidates if created_at is not None and e.get("timestamp") == created_at]
        if exact:
            candidates = exact
    if not candidates:
        return None
    version = candidates[0].get("version")
    return version if is_text_scalar(version) else None


def resolve_version(current: Any, history: Any, version: str | None, name: str, field: str) -> Resolved:
    """Resolve ``version`` (``None`` for current, else a full id or a prefix of one).

    ``field`` names the recorded object hash to return: ``"parquet_sha"`` for a
    table, ``"document_sha"`` for a set.
    """
    if version is None:
        data_sha = _recorded(current, "data_sha")
        if data_sha is None or data_sha == "":
            raise MetadataCorrupt(
                f"metadata.json for {name!r} has no `data_sha`. "
                "The metadata may be corrupt or the artifact has no data."
            )
        return Resolved(data_sha, _recorded(current, field), recorded_current_version(current, history))

    if not is_text_scalar(version):
        raise InvalidArgument("`version` must be a single non-empty string or None.")

    if not isinstance(history, list) or not history:
        raise VersionNotFound(f"No version history found for {name!r}: version_history.json is empty.")

    # Prefix matching, like git short SHAs.
    matches = [
        e for e in history if isinstance(e, dict) and isinstance(e.get("version"), str) and e["version"].startswith(version)
    ]
    if not matches:
        raise VersionNotFound(
            f"Version {version!r} not found in history for {name!r}. "
            "Use datom.history() to see available versions."
        )
    if len(matches) > 1:
        raise VersionAmbiguous(
            f"Version prefix {version!r} is ambiguous for {name!r}: it matches "
            f"{len(matches)} versions. Use a longer prefix."
        )

    entry = matches[0]
    data_sha = entry.get("data_sha")
    if data_sha is None or data_sha == "":
        raise MetadataCorrupt(f"Version {version!r} of {name!r} has no `data_sha` in its history entry.")
    return Resolved(data_sha, entry.get(field), entry.get("version"))
