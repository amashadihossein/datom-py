"""Reading a table. R sources: ``datom_read()`` and ``.datom_read_parquet()``."""

from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ._checks import check_kind, validate_name, validate_sha
from ._conn import Conn, check_conn
from ._errors import IntegrityMismatch, InvalidAddress
from ._metadata import read_metadata, resolve_version


# Integer and logical columns become pandas' nullable dtypes. The default
# conversion turns an integer column holding a missing value into float64, which
# silently rounds large integer64 values (9007199254740993 -> ...992).
_NULLABLE = {
    pa.int8(): pd.Int8Dtype(),
    pa.int16(): pd.Int16Dtype(),
    pa.int32(): pd.Int32Dtype(),
    pa.int64(): pd.Int64Dtype(),
    pa.uint8(): pd.UInt8Dtype(),
    pa.uint16(): pd.UInt16Dtype(),
    pa.uint32(): pd.UInt32Dtype(),
    pa.uint64(): pd.UInt64Dtype(),
    pa.bool_(): pd.BooleanDtype(),
}


def to_pandas(table: pa.Table) -> pd.DataFrame:
    return table.to_pandas(types_mapper=_NULLABLE.get)


def read_parquet_verified(conn: Conn, name: str, data_sha: Any, parquet_sha: Any) -> pa.Table:
    """Download ``{name}/{data_sha}.parquet``, check its SHA-256, then parse it.

    The bytes are hashed before anything parses them, so a tampered object is
    never read. An absent or empty ``parquet_sha`` (metadata written before
    integrity hashes existed) skips the check.
    """
    validate_name(name)
    if not isinstance(data_sha, str) or data_sha == "":
        raise InvalidAddress("`data_sha` must be a single non-empty string.")
    validate_sha(data_sha, "data_sha")

    key = f"{name}/{data_sha}.parquet"
    raw = conn.store.read_bytes(key)

    if parquet_sha is not None and parquet_sha != "":
        actual = hashlib.sha256(raw).hexdigest()
        if actual != parquet_sha:
            raise IntegrityMismatch(
                f"Stored parquet for {name!r} failed its integrity check. Key: {key!r}. "
                f"Expected parquet_sha: {parquet_sha!r}. Actual SHA-256: {actual!r}. "
                "The stored object may be corrupted or tampered with. Do not trust this data."
            )

    return pq.read_table(pa.BufferReader(raw))


def read(conn: Conn, name: str, version: str | None = None, *, as_arrow: bool = False) -> Any:
    """Read a table: the current version, or a past one by full id or unique prefix.

    Returns a pandas DataFrame, or a ``pyarrow.Table`` with ``as_arrow=True``.
    To read a set, use :func:`datom.get_set`.
    """
    check_conn(conn)
    validate_name(name)

    current, history = read_metadata(conn.store, name)
    check_kind(current, name, "table")
    resolved = resolve_version(current, history, version, name, "parquet_sha")
    table = read_parquet_verified(conn, name, resolved.data_sha, resolved.object_sha)
    return table if as_arrow else to_pandas(table)
