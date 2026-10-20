"""Read-only S3 store (preview).

Port of R's ``datom_store_s3()`` in ``R/store.R`` and the ``.datom_s3_*()`` reads
in ``R/utils-s3.R``, with one deliberate change: credentials arrive as a function
the caller passes in, never as values, and nothing ambient (environment
variables, ``~/.aws/``, instance metadata) may change which credentials, region
or endpoint a request uses.

botocore is imported inside functions only, so ``import datom`` and local reads
work without the ``s3`` extra. The evidence behind each botocore setting is in
``.kiro/specs/s3-reader/research-notes.md``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from ._errors import (
    AccessDenied,
    CredentialsFailed,
    CredentialsInvalid,
    DatomError,
    ExtraNotInstalled,
    InvalidAddress,
    InvalidArgument,
    NotFound,
    StoreUnreachable,
)
from ._store import build_storage_key

CredentialFunction = Callable[[], Mapping[str, Any]]

# The oldest botocore with the plugin switch the client builder turns off
# (botocore.plugin, added in 1.40.0). See design.md, "The client: nothing ambient".
MIN_BOTOCORE = (1, 40)

# The only requests a store may send (R1.4). botocore can issue others on its
# own, e.g. CreateSession for S3 Express buckets; the guard refuses those too.
ALLOWED_OPERATIONS = frozenset({"GetObject", "HeadObject", "HeadBucket"})

# botocore's refresh cannot take "no expiry", so static keys get a far-future date.
_NEVER_EXPIRES = "9999-01-01T00:00:00+00:00"

# The region in a SigV4 credential scope. Only the region group is ever used;
# the access key id before it is never captured.
_SIGNING_REGION = re.compile(r"^AWS4-HMAC-SHA256 Credential=[^/]+/\d{8}/([^/]+)/s3/aws4_request,")

# An S3 error code is copied into messages only if it looks like one.
_ERROR_CODE = re.compile(r"^[A-Za-z0-9.]{1,64}$")

_DENIED_CODES = frozenset(
    {
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "InvalidToken",
        "TokenRefreshRequired",
        "AllAccessDisabled",
        "AccountProblem",
    }
)
_MISSING_KEY_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def _now() -> datetime:
    """The clock credentials are checked against. Looked up at call time, so tests can move it."""
    return datetime.now(timezone.utc)


# --- botocore availability ---------------------------------------------------------


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


# --- credentials (R3) ---------------------------------------------------------------


def _function_name(fn: Callable) -> str:
    """A label for the credential function that never calls its ``repr()``.

    A callable object holding keys could print them from ``__repr__``, so only
    qualified names are used.
    """
    name = getattr(fn, "__qualname__", None)
    if isinstance(name, str) and name:
        return name
    return type(fn).__qualname__


def _parse_expiration(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):  # Python 3.10's fromisoformat does not read "Z"
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise CredentialsInvalid(
                "The credential function returned an `expiration` that is not an ISO-8601 date-time."
            ) from None
    else:
        raise CredentialsInvalid(
            "The credential function returned an `expiration` that is neither a datetime nor a "
            f"string (got {type(value).__name__})."
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CredentialsInvalid(
            "The credential function returned an `expiration` with no time zone; "
            "give it an offset, e.g. +00:00."
        )
    return parsed


def _parse_credentials(result: Any, now: datetime) -> dict:
    """Check one result of the credential function and put it in botocore's shape.

    Messages name the field and the problem, never a value (R3.4, R2.4).
    """
    if not isinstance(result, Mapping):
        raise CredentialsInvalid(
            f"The credential function returned {type(result).__name__}, not a mapping with "
            "`access_key` and `secret_key`."
        )
    for field in ("access_key", "secret_key"):
        value = result.get(field)
        if not isinstance(value, str) or value == "":
            problem = "is missing" if value is None else "is not a non-empty string"
            raise CredentialsInvalid(f"The credential function's `{field}` {problem}.")
    token = result.get("session_token")
    if token is not None and (not isinstance(token, str) or token == ""):
        raise CredentialsInvalid(
            "The credential function's `session_token` must be a non-empty string or absent."
        )
    expiration = _parse_expiration(result.get("expiration"))
    if expiration is not None and expiration <= now:
        raise CredentialsInvalid(
            "The credential function returned credentials that have already expired."
        )
    return {
        "access_key": result["access_key"],
        "secret_key": result["secret_key"],
        "token": token,
        "expiry_time": expiration.isoformat() if expiration is not None else _NEVER_EXPIRES,
    }


def _refresher(credentials: CredentialFunction) -> Callable[[], dict]:
    """What botocore calls for (fresh) credentials: the caller's function, checked."""

    def refresh() -> dict:
        try:
            result = credentials()
        except Exception as e:
            # The exception's text is not copied: a user function could have put a key in it.
            raise CredentialsFailed(
                f"The credential function {_function_name(credentials)} raised "
                f"{type(e).__name__}; see the chained exception."
            ) from e
        return _parse_credentials(result, _now())

    return refresh


# --- the client: nothing ambient (R4) -----------------------------------------------


class _RequestRefused(Exception):
    """Raised by the request guard; turned into a datom error by the store."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _request_guard(region: str) -> Callable:
    """A ``before-send`` handler: only the allowed operations, only signed for ``region``.

    botocore follows S3's "bucket is in another region" redirect by itself and
    re-signs for that region; this refuses the re-sent request instead.
    """

    def guard(request, event_name: str, **_: Any) -> None:
        operation = event_name.rsplit(".", 1)[-1]
        if operation not in ALLOWED_OPERATIONS:
            raise _RequestRefused(f"datom does not send {operation} requests")
        auth = request.headers.get("Authorization") or b""
        if isinstance(auth, bytes):
            auth = auth.decode("latin-1")
        match = _SIGNING_REGION.match(auth)
        signed_for = match.group(1) if match else None
        if signed_for != region:
            raise _RequestRefused(
                f"the bucket is in region {signed_for!r}, not {region!r}; pass region={signed_for!r}"
                if signed_for
                else "the request was not signed with the store's credentials"
            )

    return guard


def _build_client(region: str, credentials: CredentialFunction):
    """An S3 client that only the arguments to ``store_s3()`` can influence.

    Each replaced component was shown necessary by removing it alone and
    watching a planted setting get through (research notes, section 3.2).
    """
    import botocore.session
    from botocore.configprovider import ConfigValueStore, ConstantProvider
    from botocore.credentials import (
        CredentialProvider,
        CredentialResolver,
        DeferredRefreshableCredentials,
    )
    from botocore.loaders import Loader
    from botocore.plugin import PluginContext, reset_plugin_context, set_plugin_context
    from botocore.tokens import TokenProviderChain

    class _Provider(CredentialProvider):
        # botocore asks providers from outside botocore to start with "custom".
        METHOD = CANONICAL_NAME = "custom-datom"

        def load(self):
            return DeferredRefreshableCredentials(
                _refresher(credentials), self.METHOD, time_fetcher=lambda: _now()
            )

    session = botocore.session.Session()
    # Every setting a constant at botocore's default: no environment variable,
    # config file or credentials file is consulted, and configured endpoints
    # (AWS_ENDPOINT_URL*, endpoint_url, services sections) are ignored.
    values = {name: spec[2] for name, spec in session.SESSION_VARIABLES.items()}
    values.update(config_file=None, credentials_file=None, ignore_configured_endpoint_urls=True)
    session.register_component(
        "config_store",
        ConfigValueStore(mapping={k: ConstantProvider(v) for k, v in values.items()}),
    )
    # botocore's own data only; not ~/.aws/models.
    session.register_component(
        "data_loader",
        Loader(extra_search_paths=[Loader.BUILTIN_DATA_PATH], include_default_search_paths=False),
    )
    # One credential provider: ours. No env, files, credential_process, SSO,
    # web identity, container or instance metadata.
    session.register_component("credential_provider", CredentialResolver(providers=[_Provider()]))
    # No bearer token (AWS_BEARER_TOKEN_S3).
    session.register_component("token_provider", TokenProviderChain(providers=[]))
    # No client plugins (BOTOCORE_EXPERIMENTAL__PLUGINS).
    token = set_plugin_context(PluginContext(plugins="DISABLED"))
    try:
        client = session.create_client("s3", region_name=region)
    finally:
        reset_plugin_context(token)
    client.meta.events.register_first("before-send.s3", _request_guard(region))
    return client


# --- debug logs (R2.4) --------------------------------------------------------------


class _SecretLogFilter(logging.Filter):
    """Drops botocore debug records that carry the access key id or session token.

    botocore logs request headers and the canonical request at DEBUG; the secret
    key itself is never logged. Installed on botocore's loggers, so it applies to
    every botocore client in the process (design decision 3).
    """

    MARKERS = ("x-amz-security-token", "authorization", "credential=", "awsaccesskeyid")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage().lower()
        except Exception:
            return False
        return not any(marker in text for marker in self.MARKERS)


_FILTERED_LOGGERS = ("botocore.endpoint", "botocore.auth", "botocore.parsers")


def _install_log_filter() -> None:
    for name in _FILTERED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, _SecretLogFilter) for f in logger.filters):
            logger.addFilter(_SecretLogFilter())


# --- errors (R2) --------------------------------------------------------------------


def _client_error_details(error) -> tuple[str, int | None]:
    response = getattr(error, "response", None) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    if not _ERROR_CODE.match(code):
        code = "unrecognised"
    return code, status if isinstance(status, int) else None


def _describe(code: str, status: int | None) -> str:
    return f"{code}, HTTP {status}" if status is not None else code


# --- the store ----------------------------------------------------------------------


class S3Store:
    """A datom store in an S3 bucket. Use :func:`datom.store_s3` to make one.

    Reads only: :meth:`check_reachable`, :meth:`exists`, :meth:`read_bytes`. Holds
    the credential *function*, never its result (R5.1); resolved credentials live
    only inside the botocore client, which is built on first use and dropped on
    pickling.
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

    def __getstate__(self) -> dict:
        state = self.__dict__.copy()
        state["_client"] = None  # holds resolved credentials; rebuilt on first use
        return state

    def _get_client(self):
        if self._client is None:
            _install_log_filter()
            self._client = _build_client(self.region, self._credentials)
        return self._client

    def _object_key(self, key: str) -> str:
        # Callers only splice validated names and addresses into keys; this is a
        # second line of defence, as in LocalStore._path().
        if any(part in (".", "..") for part in key.split("/")):
            raise InvalidAddress(f"Storage key {key!r} escapes the datom namespace.")
        return build_storage_key(self.prefix, key)

    def _where(self, object_key: str | None) -> str:
        return f"s3://{self.bucket}/{object_key}" if object_key else f"bucket {self.bucket!r}"

    def _request(self, operation: str, object_key: str | None = None):
        """Send one request; return the response, or raise the mapped datom error.

        The datom error is raised after the ``except`` block, so no botocore
        exception (whose text and response can carry server-supplied content) is
        attached to it.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        client = self._get_client()
        params = {"Bucket": self.bucket}
        if object_key is not None:
            params["Key"] = object_key
        failure: DatomError | None = None
        try:
            if operation == "HeadBucket":
                return client.head_bucket(**params)
            if operation == "HeadObject":
                return client.head_object(**params)
            response = client.get_object(**params)
            return response["Body"].read()
        except DatomError:
            raise  # e.g. CredentialsInvalid / CredentialsFailed from the refresher
        except _RequestRefused as e:
            failure = StoreUnreachable(f"Refused a request to {self._where(object_key)}: {e.reason}.")
        except ClientError as e:
            failure = self._map_client_error(operation, object_key, *_client_error_details(e))
        except BotoCoreError as e:
            failure = StoreUnreachable(
                f"Could not reach {self._where(object_key)} in region {self.region!r} "
                f"({type(e).__name__})."
            )
        raise failure

    def _map_client_error(
        self, operation: str, object_key: str | None, code: str, status: int | None
    ) -> DatomError:
        where = self._where(object_key)
        detail = _describe(code, status)
        if code == "NoSuchBucket" or (operation == "HeadBucket" and status == 404):
            return StoreUnreachable(f"S3 bucket {self.bucket!r} does not exist ({detail}).")
        if code in _MISSING_KEY_CODES or status == 404:
            return NotFound(f"Not found in S3: {where} ({detail}).")
        if code in _DENIED_CODES or status == 403:
            if operation == "HeadBucket":
                return AccessDenied(
                    f"S3 refused access to bucket {self.bucket!r} ({detail}). Connecting checks "
                    "the bucket with HeadBucket, which needs the s3:ListBucket permission; check "
                    "the credentials and that permission."
                )
            return AccessDenied(f"S3 refused access to {where} ({detail}).")
        if status in (301, 307) or code in ("PermanentRedirect", "AuthorizationHeaderMalformed"):
            return StoreUnreachable(
                f"S3 says bucket {self.bucket!r} is not in region {self.region!r} ({detail}); "
                "pass the bucket's region."
            )
        return StoreUnreachable(f"S3 request to {where} failed ({detail}).")

    def check_reachable(self) -> None:
        """``HeadBucket``, as R's ``.datom_check_data_reachable()`` does (decision 1)."""
        self._request("HeadBucket")

    def exists(self, key: str) -> bool:
        """``HeadObject``; ``False`` only on a 404, as R's ``.datom_s3_exists()``."""
        try:
            self._request("HeadObject", self._object_key(key))
        except NotFound:
            return False
        return True

    def read_bytes(self, key: str) -> bytes:
        """``GetObject``; raises :class:`NotFound` if the object is not there."""
        return self._request("GetObject", self._object_key(key))


# --- the constructor ----------------------------------------------------------------


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
    ``expiration`` (a timezone-aware datetime or ISO-8601 string; absent means
    the keys do not expire). It is not called here: datom calls it when it first
    needs credentials, and again before they expire. Nothing else is ever used
    for credentials, region or endpoint: not environment variables, not
    ``~/.aws/``, not instance metadata.

    A bucket in a different region from ``region`` is refused, with a message
    naming the bucket's region.

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
