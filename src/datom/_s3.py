"""Read-only S3 store (preview).

Port of R's ``datom_store_s3()`` in ``R/store.R``, with one deliberate change:
credentials arrive as a function the caller passes in, never as values, and
nothing ambient (environment variables, ``~/.aws/``, instance metadata) may
change which credentials, region or endpoint a request uses.

botocore is imported inside functions only, so ``import datom`` and local reads
work without the ``s3`` extra.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from ._errors import ExtraNotInstalled, InvalidArgument

CredentialFunction = Callable[[], Mapping[str, Any]]

# The oldest botocore with the plugin switch the client builder turns off
# (botocore.plugin, added in 1.40.0). See design.md, "The client: nothing ambient".
MIN_BOTOCORE = (1, 40)


def _require_botocore() -> None:
    """Import botocore, or refuse with the install hint (R7.2)."""
    try:
        import botocore
    except ImportError as e:
        raise ExtraNotInstalled(
            'S3 stores need boto3. Install it with: pip install "datom[s3]"'
        ) from e
    version = tuple(int(p) for p in botocore.__version__.split(".")[:2] if p.isdigit())
    if version < MIN_BOTOCORE:
        raise ExtraNotInstalled(
            f"S3 stores need botocore {MIN_BOTOCORE[0]}.{MIN_BOTOCORE[1]} or later; "
            f"found {botocore.__version__}. Upgrade with: pip install -U \"datom[s3]\""
        )


def _function_name(fn: Callable) -> str:
    """A label for the credential function that never calls its ``repr()``.

    A callable object holding keys could print them from ``__repr__``, so only
    qualified names are used.
    """
    name = getattr(fn, "__qualname__", None)
    if isinstance(name, str) and name:
        return name
    return type(fn).__qualname__


class S3Store:
    """A datom store in an S3 bucket. Use :func:`datom.store_s3` to make one.

    Holds the credential *function*, never its result (R5.1).
    """

    backend = "s3"

    def __init__(
        self,
        bucket: str,
        prefix: str | None,
        region: str,
        credentials: CredentialFunction,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.region = region
        self._credentials = credentials
        self._client = None

    def __repr__(self) -> str:
        return (
            f"S3Store(bucket={self.bucket!r}, prefix={self.prefix!r}, region={self.region!r}, "
            f"credentials=<function {_function_name(self._credentials)}>)"
        )


def _check_arguments(bucket, prefix, region, credentials) -> None:
    """Argument shapes, as R's ``datom_store_s3()`` checks them."""
    if not isinstance(bucket, str) or bucket == "":
        raise InvalidArgument("`bucket` must be a non-empty string.")
    if prefix is not None and not isinstance(prefix, str):
        raise InvalidArgument("`prefix` must be a string or None.")
    if not isinstance(region, str) or region == "":
        raise InvalidArgument("`region` must be a non-empty string.")
    if not callable(credentials):
        raise InvalidArgument(
            "`credentials` must be a function taking no arguments and returning a mapping "
            "with `access_key` and `secret_key`."
        )


def store_s3(
    bucket: str,
    prefix: str | None = None,
    region: str = "us-east-1",
    *,
    credentials: CredentialFunction,
) -> S3Store:
    """A read-only store over an S3 bucket that R datom wrote to.

    ``bucket``, ``prefix`` and ``region`` mean what they mean in R's
    ``datom_store_s3()``; the data lives under ``s3://{bucket}/{prefix}/datom/``.

    ``credentials`` is a function taking no arguments and returning a mapping
    with ``access_key``, ``secret_key`` and optionally ``session_token`` and
    ``expiration``. It is not called here: datom calls it when it first needs
    credentials, and again before they expire. Nothing else is ever used for
    credentials, region or endpoint: not environment variables, not ``~/.aws/``,
    not instance metadata.

    Needs the ``s3`` extra: ``pip install "datom[s3]"``.
    """
    _check_arguments(bucket, prefix, region, credentials)
    _require_botocore()
    from botocore.exceptions import InvalidRegionError
    from botocore.utils import validate_region_name

    try:
        validate_region_name(region)
    except InvalidRegionError:
        raise InvalidArgument(f"`region` {region!r} is not a valid AWS region name.") from None
    return S3Store(bucket, prefix, region, credentials)
