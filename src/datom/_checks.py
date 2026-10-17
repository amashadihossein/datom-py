"""The checks every read makes, ported from R datom.

R sources: ``.datom_validate_name()``, ``.datom_validate_sha()``,
``.datom_check_schema_version()`` (``R/utils-validate.R``) and
``.datom_check_artifact_kind()`` (``R/read_write.R``).
"""

from __future__ import annotations

import re
from typing import Any

from ._errors import InvalidAddress, InvalidName, KindMismatch, SchemaInvalid, SchemaUnsupported

#: Highest metadata/manifest format this package reads (R: ``.datom_supported_schema``).
SUPPORTED_SCHEMA = 2

RESERVED_NAMES = frozenset({".metadata", ".datom", "input_files", "datom", ".git", ".gitignore", "renv"})

_NAME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_ ()-]*")
_SHA_RE = re.compile(r"[0-9a-f]{6,64}")

# The function that reads each kind, for messages that point at the right one.
_READER_FOR_KIND = {"table": "read", "set": "get_set"}


def is_text_scalar(x: Any) -> bool:
    """R's ``.datom_is_text_scalar()``: a single non-empty string."""
    return isinstance(x, str) and x != ""


def validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise InvalidName("`name` must be a string.")
    if name == "":
        raise InvalidName("`name` must not be empty.")
    if len(name) > 128:
        raise InvalidName(f"`name` must be 128 characters or fewer (got {len(name)}).")
    if not _NAME_RE.fullmatch(name):
        raise InvalidName(
            "`name` must start with a letter and contain only letters, numbers, "
            f"underscores, hyphens, spaces and parentheses. Got: {name!r}"
        )
    if name.lower() in RESERVED_NAMES:
        raise InvalidName(f"{name!r} is a reserved name.")
    return name


def validate_sha(x: Any, arg: str = "data_sha") -> str:
    """A data address must be 6 to 64 lowercase hex characters.

    Checked before the address is spliced into a storage path, so ``../x`` and
    the like never reach the store.
    """
    if not isinstance(x, str) or not _SHA_RE.fullmatch(x):
        raise InvalidAddress(f"`{arg}` must be 6-64 lowercase hex characters. Got: {x!r}")
    return x


def check_schema_version(doc: Any, source: str) -> int:
    """Return the format version a document declares, or refuse it.

    Absent (or null) means 1. A whole number up to :data:`SUPPORTED_SCHEMA`
    proceeds; a larger one is :class:`SchemaUnsupported`; anything else is
    :class:`SchemaInvalid`.
    """
    declared = doc.get("schema_version") if isinstance(doc, dict) else None
    if declared is None:
        return 1

    # bool is an int subclass in Python; R's is.numeric(TRUE) is FALSE.
    usable = (
        isinstance(declared, (int, float))
        and not isinstance(declared, bool)
        and declared == declared  # not NaN
        and declared >= 1
        and float(declared).is_integer()
    )
    if not usable:
        raise SchemaInvalid(
            f"`schema_version` in {source!r} is not a usable schema version. Got: {declared!r}. "
            "Expected a single whole number, e.g. 2. The document may be corrupt or hand-edited."
        )

    declared = int(declared)
    if declared > SUPPORTED_SCHEMA:
        raise SchemaUnsupported(
            f"This repo uses datom schema v{declared}, which this build cannot read. "
            f"Declared by {source!r}. This package supports up to v{SUPPORTED_SCHEMA}; "
            "upgrade it."
        )
    return declared


def check_kind(current: Any, name: str, expected: str) -> None:
    """Refuse a document that describes a different kind of artifact.

    An absent ``kind`` means ``"table"``: every document written before the
    field existed described a table. Runs before the data path is built, since
    the payload's address follows the kind.
    """
    if not isinstance(current, dict):
        return
    found = current.get("kind")
    if found is None:
        found = "table"
    if found == expected:
        return
    verb = _READER_FOR_KIND.get(found, "read") if isinstance(found, str) else "read"
    raise KindMismatch(
        f"{name!r} is a {found}, not a {expected}. Read it with datom.{verb}(). "
        "One name is one artifact: a table resolves to data, a set to references."
    )
