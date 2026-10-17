"""Reading a set. R sources: ``datom_get_set()``, ``.datom_read_set_payload()``,
``.datom_read_set_member()`` (``R/set.R``) and ``datom_fetch_member()``
(``R/set-members.R``).

A set's content is pointers plus labels. Reading one never fetches member data
and never follows a member that is itself a set. A member is resolved only when
asked for, with :func:`fetch_member` or :meth:`Member.fetch`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ._checks import check_kind, is_text_scalar, validate_name, validate_sha
from ._conn import Conn, check_conn
from ._errors import (
    DatomError,
    DocumentShaMissing,
    DocumentUnreadable,
    IntegrityMismatch,
    InvalidAddress,
    InvalidArgument,
    MemberAmbiguous,
    MemberKindUnknown,
    MemberNotFound,
    MemberProjectMismatch,
    SetMemberMalformed,
    SetPayloadMalformed,
)
from ._metadata import read_metadata, resolve_version

_ID_FIELDS = ("project", "name", "kind", "version")


@dataclass(frozen=True)
class MemberId:
    """The full citation of one member: project, name, kind and version."""

    project: str
    name: str
    kind: str
    version: str


@dataclass(frozen=True)
class Member:
    """One member of a set: a pointer, not data."""

    id: MemberId
    #: Labels, each a list of strings. ``None`` when the member has no ``tags``.
    tags: dict | None = None

    def fetch(self, conn: Conn, *, as_arrow: bool = False) -> Any:
        """Read this member through ``conn``, a connection to the member's project."""
        return _fetch(conn, self, as_arrow=as_arrow)


@dataclass(frozen=True)
class DatomSet:
    """A set read from storage: its identity, labels and members (as pointers)."""

    name: str
    project: str
    version: str | None
    data_sha: str
    tags: dict | None
    members: tuple[Member, ...]

    def __repr__(self) -> str:
        version = self.version[:8] if isinstance(self.version, str) else None
        return (
            f"DatomSet(name={self.name!r}, project={self.project!r}, version={version!r}, "
            f"members={len(self.members)})"
        )


# --- representation only ----------------------------------------------------
#
# The read never tidies: it reports what was stored. The one normalisation is
# representation, so that a label is always a list of strings, whether the JSON
# held "x" or ["x"].


def _string_array(v: Any) -> Any:
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and v and all(isinstance(e, str) for e in v):
        return list(v)
    return v


def _tag_map(tags: Any) -> Any:
    if not isinstance(tags, dict) or not tags:
        return tags
    return {k: _string_array(v) for k, v in tags.items()}


def _id_text(v: Any) -> Any:
    # R reads a one-element string array as that string.
    if isinstance(v, list) and len(v) == 1 and isinstance(v[0], str):
        return v[0]
    return v


def _read_member(m: Any, at: str, name: str) -> Member:
    def malformed(why: str) -> SetMemberMalformed:
        return SetMemberMalformed(
            f"The stored payload for set {name!r} is malformed: {why} "
            "Run datom_validate() on the project that owns this set."
        )

    if not isinstance(m, dict) or not m or not all(isinstance(k, str) and k for k in m):
        raise malformed(f"{at} is not a named record.")
    raw_id = m.get("id")
    if not isinstance(raw_id, dict) or not raw_id:
        raise malformed(f"{at} has no `id` map.")
    ident = {k: _id_text(v) for k, v in raw_id.items()}
    for field in _ID_FIELDS:
        if not is_text_scalar(ident.get(field)):
            raise malformed(f"{at}: `id.{field}` is not a single non-empty string.")

    tags = _tag_map(m["tags"]) if "tags" in m else None
    return Member(id=MemberId(*(ident[f] for f in _ID_FIELDS)), tags=tags)


def _read_members(members: Any, name: str) -> tuple[Member, ...]:
    if not isinstance(members, list):
        raise SetPayloadMalformed(
            f"The stored payload for set {name!r} is malformed: `members` is not a list of "
            "member records."
        )
    return tuple(_read_member(m, f"members[{i}]", name) for i, m in enumerate(members))


def read_set_payload(conn: Conn, name: str, data_sha: Any, document_sha: Any) -> Any:
    """Download ``{name}/{data_sha}.json``, check its SHA-256, then parse it.

    A set with no ``document_sha`` is an error, not a skipped check: sets have
    recorded one since their first write.
    """
    validate_name(name)
    if not is_text_scalar(data_sha):
        raise InvalidAddress("`data_sha` must be a single non-empty string.")
    validate_sha(data_sha, "data_sha")
    if not is_text_scalar(document_sha):
        raise DocumentShaMissing(
            f"The recorded metadata for set {name!r} carries no `document_sha`, so this "
            "version cannot be verified. Run datom_validate() on this project."
        )

    key = f"{name}/{data_sha}.json"
    raw = conn.store.read_bytes(key)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != document_sha:
        raise IntegrityMismatch(
            f"Stored payload for set {name!r} failed its integrity check. Key: {key!r}. "
            f"Expected document_sha: {document_sha!r}. Actual SHA-256: {actual!r}. "
            "The stored object may be corrupted or tampered with. Do not trust this set."
        )
    try:
        return json.loads(raw)
    except ValueError as e:
        raise DocumentUnreadable(f"Failed to parse the payload of set {name!r}: {e}") from e


def get_set(conn: Conn, name: str, version: str | None = None) -> DatomSet:
    """Read a set: references and labels, no member data.

    Members come back as :class:`Member` pointers, in payload order. Nothing is
    fetched, and a member that is itself a set is not followed.
    """
    check_conn(conn)
    validate_name(name)

    current, history = read_metadata(conn.store, name)
    check_kind(current, name, "set")
    resolved = resolve_version(current, history, version, name, "document_sha")
    payload = read_set_payload(conn, name, resolved.data_sha, resolved.object_sha)
    if not isinstance(payload, dict):
        raise SetPayloadMalformed(f"The stored payload for set {name!r} is not a JSON object.")

    project = current.get("project") if isinstance(current, dict) else None
    return DatomSet(
        name=name,
        # The name the set's metadata records, else the connection's. Never read
        # from the manifest: the data path does not touch that document.
        project=project if is_text_scalar(project) else conn.project_name,
        version=resolved.version,
        data_sha=resolved.data_sha,
        tags=_tag_map(payload.get("tags")),
        members=_read_members(payload.get("members"), name),
    )


# --- one member ---------------------------------------------------------------


def _has_tags(member: Member, tags: dict) -> bool:
    have_map = member.tags if isinstance(member.tags, dict) else {}
    for key, want in tags.items():
        want = [want] if isinstance(want, str) else list(want)
        have = have_map.get(key)
        have = [v for v in have if isinstance(v, str)] if isinstance(have, list) else []
        if not have or not all(w in have for w in want):
            return False
    return True


def _describe(members: list[Member]) -> str:
    return "; ".join(f"{m.id.name} @ {m.id.version[:8]} {m.tags or {}}" for m in members)


def find_member(members: tuple[Member, ...], name: str, tags: dict | None = None, version: str | None = None) -> Member:
    named = [m for m in members if m.id.name == name]
    if not named:
        available = sorted({m.id.name for m in members})
        raise MemberNotFound(
            f"This set has no member named {name!r}. "
            + (f"It has {available}." if available else "The set has no members at all.")
        )

    narrowed = named
    if tags is not None:
        narrowed = [m for m in narrowed if _has_tags(m, tags)]
    if version is not None:
        narrowed = [m for m in narrowed if m.id.version.startswith(version)]

    if not narrowed:
        raise MemberNotFound(
            f"No member named {name!r} matches what you narrowed by. "
            f"{len(named)} member(s) named {name!r}: {_describe(named)}"
        )
    if len(narrowed) > 1:
        raise MemberAmbiguous(
            f"{name!r} names {len(narrowed)} members of this set (the same artifact at two "
            "versions is legal). Narrow by label, e.g. tags={'role': 'baseline'}, or pin "
            f"one with version=. Candidates: {_describe(narrowed)}"
        )
    return narrowed[0]


def _fetch(conn: Conn, member: Member, *, as_arrow: bool) -> Any:
    from ._table import read  # avoid an import cycle

    ident = member.id
    try:
        if ident.kind == "table":
            return read(conn, ident.name, version=ident.version, as_arrow=as_arrow)
        if ident.kind == "set":
            return get_set(conn, ident.name, version=ident.version)
        raise MemberKindUnknown(
            f"Member {ident.name!r} is a {ident.kind!r}, which this package cannot resolve. "
            "The set may have been written by a newer datom."
        )
    except DatomError as e:
        ours = conn.project_name if isinstance(conn, Conn) else None
        if is_text_scalar(ident.project) and is_text_scalar(ours) and ident.project != ours:
            raise MemberProjectMismatch(
                f"Could not fetch member {ident.name!r} through this connection. That "
                f"{ident.kind} belongs to project {ident.project!r}, and this connection is "
                f"for {ours!r}. Open a connection to {ident.project!r} and fetch it through "
                f"that one. Cause: {e}"
            ) from e
        raise


def fetch_member(
    conn: Conn,
    x: DatomSet,
    member: str | Member,
    tags: dict | None = None,
    version: str | None = None,
    *,
    as_arrow: bool = False,
) -> Any:
    """Read one member's data (or, for a member that is a set, its pointers).

    ``member`` is a name or a :class:`Member`. A name that matches several
    members is refused; narrow it with ``tags`` (labels the member carries) or
    ``version`` (a prefix). ``conn`` must be a connection to the member's own
    project.
    """
    check_conn(conn)
    if not isinstance(x, DatomSet):
        raise InvalidArgument("`x` must be a set from datom.get_set().")
    if tags is not None:
        ok = isinstance(tags, dict) and tags and all(
            isinstance(k, str) and k and (is_text_scalar(v) or (isinstance(v, list) and v and all(is_text_scalar(e) for e in v)))
            for k, v in tags.items()
        )
        if not ok:
            raise InvalidArgument("`tags` must map label names to a string or a list of strings.")
    if version is not None and not is_text_scalar(version):
        raise InvalidArgument("`version` must be a single non-empty string.")

    if isinstance(member, Member):
        if tags is not None or version is not None:
            raise InvalidArgument(
                "`tags` and `version` narrow a member name; a Member already names one member."
            )
        record = member
    elif is_text_scalar(member):
        record = find_member(x.members, member, tags, version)
    else:
        raise InvalidArgument("`member` must be a member name or a Member.")

    return _fetch(conn, record, as_arrow=as_arrow)
