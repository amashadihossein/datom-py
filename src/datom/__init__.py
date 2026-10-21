"""datom for Python: a read-only reader for data written by R datom.

Early development. Reads tables, sets, history and listings from a local-folder
store, or (preview, ``pip install "datom[s3]"``) an S3 bucket, with the same
checks R datom makes before handing over data.

    import datom

    store = datom.store_local("path/to/storage")
    # or: datom.store_s3("my-bucket", prefix="proj", credentials=my_credential_function)
    conn = datom.get_conn(store, project_name="STUDY_001")

    df = datom.read(conn, "dm")                       # current version, pandas
    df = datom.read(conn, "dm", version="a3f8c1d2")   # by version prefix
    tbl = datom.read(conn, "dm", as_arrow=True)       # pyarrow Table

    datom.history(conn, "dm")
    datom.list_artifacts(conn)

    s = datom.get_set(conn, "trial_product")          # members as pointers
    datom.fetch_member(conn, s, "dm", tags={"role": "current"})
"""

from ._conn import Conn, get_conn
from ._errors import (
    AccessDenied,
    CredentialsFailed,
    CredentialsInvalid,
    DatomError,
    DocumentShaMissing,
    DocumentUnreadable,
    ExtraNotInstalled,
    GovernanceAttachedWarning,
    IntegrityMismatch,
    InvalidAddress,
    InvalidArgument,
    InvalidName,
    KindMismatch,
    ManifestUnreadable,
    MemberAmbiguous,
    MemberKindUnknown,
    MemberNotFound,
    MemberProjectMismatch,
    MetadataCorrupt,
    NotFound,
    SchemaInvalid,
    SchemaUnsupported,
    SetMemberMalformed,
    SetPayloadMalformed,
    StoreUnreachable,
    VersionAmbiguous,
    VersionNotFound,
)
from ._query import history, list_artifacts
from ._s3 import S3Store, store_s3
from ._sso import SsoCredentials, sso_credentials
from ._set import DatomSet, Member, MemberId, fetch_member, get_set
from ._store import LocalStore, store_local
from ._table import read

__version__ = "0.1.0"

__all__ = [
    "AccessDenied",
    "Conn",
    "CredentialsFailed",
    "CredentialsInvalid",
    "DatomError",
    "DatomSet",
    "DocumentShaMissing",
    "DocumentUnreadable",
    "ExtraNotInstalled",
    "GovernanceAttachedWarning",
    "IntegrityMismatch",
    "InvalidAddress",
    "InvalidArgument",
    "InvalidName",
    "KindMismatch",
    "LocalStore",
    "ManifestUnreadable",
    "Member",
    "MemberAmbiguous",
    "MemberId",
    "MemberKindUnknown",
    "MemberNotFound",
    "MemberProjectMismatch",
    "MetadataCorrupt",
    "NotFound",
    "S3Store",
    "SchemaInvalid",
    "SchemaUnsupported",
    "SetMemberMalformed",
    "SetPayloadMalformed",
    "SsoCredentials",
    "StoreUnreachable",
    "VersionAmbiguous",
    "VersionNotFound",
    "fetch_member",
    "get_conn",
    "get_set",
    "history",
    "list_artifacts",
    "read",
    "store_local",
    "sso_credentials",
    "store_s3",
]
