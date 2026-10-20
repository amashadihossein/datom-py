"""Errors and warnings, each with a language-neutral ``code``.

The codes are the names the shared reader cases use (``case.json`` ->
``expect.error``), so a test can match a refusal without depending on message
wording. They line up with R datom's condition classes where R has one; see
``tests/reader-cases/README.md`` for the table.
"""

from __future__ import annotations


class DatomError(Exception):
    """Base class for every refusal this package raises."""

    code = "datom_error"


class InvalidArgument(DatomError, ValueError):
    """A caller passed an argument of the wrong shape."""

    code = "invalid_argument"


class InvalidName(InvalidArgument):
    """An artifact name that datom would never write."""

    code = "invalid_name"


class InvalidAddress(DatomError):
    """A data address (``data_sha``) that isn't 6 to 64 lowercase hex characters.

    Raised before any storage path is built, because the address is spliced into
    the path.
    """

    code = "invalid_address"


class StoreUnreachable(DatomError):
    """The store's root can't be reached (for a local store: the folder is missing)."""

    code = "store_unreachable"


class NotFound(DatomError):
    """A storage object the read needs is not there."""

    code = "not_found"


class AccessDenied(DatomError):
    """The storage backend refused the credentials, or refused access to the bucket or an object."""

    code = "access_denied"


class CredentialsInvalid(DatomError):
    """The credential function returned something malformed, or credentials already expired.

    The message names the field and the problem, never a value.
    """

    code = "credentials_invalid"


class CredentialsFailed(DatomError):
    """The credential function raised. The original exception is chained; its text is not copied."""

    code = "credentials_failed"


class ExtraNotInstalled(DatomError):
    """An optional dependency is missing, e.g. ``pip install "datom[s3]"`` for S3 stores."""

    code = "extra_not_installed"


class DocumentUnreadable(DatomError):
    """A stored JSON document could not be parsed."""

    code = "document_unreadable"


class SchemaUnsupported(DatomError):
    """A document declares a newer format than this package supports."""

    code = "schema_unsupported"


class SchemaInvalid(DatomError):
    """A document's ``schema_version`` is not a usable whole number."""

    code = "schema_invalid"


class KindMismatch(DatomError):
    """A set read as a table, or a table read as a set."""

    code = "kind_mismatch"


class MetadataCorrupt(DatomError):
    """Metadata is missing a field every datom write records (e.g. ``data_sha``)."""

    code = "metadata_corrupt"


class VersionNotFound(DatomError):
    """The requested version matches nothing in the history."""

    code = "version_not_found"


class VersionAmbiguous(DatomError):
    """The requested version prefix matches more than one version."""

    code = "version_ambiguous"


class IntegrityMismatch(DatomError):
    """Stored bytes don't match their recorded SHA-256. Nothing was parsed."""

    code = "integrity_mismatch"


class DocumentShaMissing(DatomError):
    """A set version records no ``document_sha``, so it can't be verified."""

    code = "document_sha_missing"


class SetPayloadMalformed(DatomError):
    """A set payload whose ``members`` is not a list of member records."""

    code = "set_payload_malformed"


class SetMemberMalformed(DatomError):
    """A set member whose record or ``id`` is malformed."""

    code = "set_member_malformed"


class MemberNotFound(DatomError):
    """No member of the set matches the name (and any narrowing) given."""

    code = "member_not_found"


class MemberAmbiguous(DatomError):
    """More than one member matches; narrow by ``tags`` or ``version``."""

    code = "member_ambiguous"


class MemberKindUnknown(DatomError):
    """A member points at a kind of artifact this package can't resolve."""

    code = "member_kind_unknown"


class MemberProjectMismatch(DatomError):
    """A member from another project failed to fetch through this connection."""

    code = "member_project_mismatch"


class ManifestUnreadable(DatomError):
    """The project manifest can't be read, or has no artifact list."""

    code = "manifest_unreadable"


class GovernanceAttachedWarning(UserWarning):
    """The project has governance attached, but the connection used a direct location."""

    code = "governance_attached"
