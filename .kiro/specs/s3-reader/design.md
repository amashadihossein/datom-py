# Design -- S3 reader (preview first)

**Requirements**: `requirements.md` (approved 2026-10-17). They stay the production target.
**Approach (owner, 2026-10-19)**: ship a preview that covers the core route, then add to the same code
in later tasks. Nothing in the preview is written to be thrown away.
**Evidence**: `research-notes.md` holds the raw findings behind every botocore claim here, each tagged
verified or unverified (botocore 1.40.0 and 1.43.114, probed under moto on Python 3.14).

## Scope split

| Preview (this design, first tasks) | Hardening (later tasks, same spec) |
|---|---|
| `store_s3()` with the three read operations, plugged into the existing reader (R1) | `sso_credentials()` (R6) |
| Credentials only from the caller's function, deferred and refreshed (R3) | Planting wrong settings in *every* ambient source, one at a time (full AC4) |
| An S3 connection that nothing ambient can influence (R4) | Pickle tests (AC8 pickle part) |
| Error mapping to `not_found`, `access_denied`, `store_unreachable` (R2) | Probing each check's test going red with the check removed (AC11) |
| No secret in `repr()`, error messages or botocore debug logs (R2.4, R5.1) | README credential guidance and keyring example (R9.2-R9.3) |
| boto3 as the optional extra `datom[s3]` (R7) | A CI job on the newest botocore |
| Every shared reader case on a fake S3, with and without a prefix (R8.1) | |

The reason the "nothing ambient" rule is in the preview: a store that quietly picks up `~/.aws/` keys
looks like it works, so adding the rule later would change behaviour people already rely on.

## Modules

- `src/datom/_s3.py` (new): `S3Store`, `store_s3()`, the client builder, the error mapping. Imports
  botocore inside functions only, so `import datom` never loads it (R7.1).
- `src/datom/_errors.py`: new codes (see Open decisions 1).
- `src/datom/__init__.py`: export `S3Store`, `store_s3`.
- `pyproject.toml`: extra `s3 = ["boto3>=1.40"]` (pins botocore 1.40 or later, the oldest with the plugin
  switch used below); `dev` gains `moto[s3]>=5`.
- Nothing else in `src/` changes. `get_conn()` already duck-types the store and calls
  `check_reachable()`; every read rule calls only `store.read_bytes()` / `store.exists()` (R1.5).

## `S3Store`

Same shape as `LocalStore` in `_store.py`:

| Method | S3 request | Result |
|---|---|---|
| `check_reachable()` | `HeadBucket` | `None`, or the mapped error |
| `exists(key)` | `HeadObject` | `True` / `False` on 404 / mapped error otherwise |
| `read_bytes(key)` | `GetObject` | bytes, or the mapped error (`NotFound` on 404) |

- Keys are `build_storage_key(prefix, key)` from `_store.py` (R1.2). Keys with a `.` or `..` segment
  are refused with `InvalidAddress`, mirroring the local store's namespace check.
- Attributes: `bucket`, `prefix`, `region`, `_credentials` (the function), `_client` (built lazily on
  first request, `None` until then). Nothing else.
- `__repr__`: `S3Store(bucket=..., prefix=..., region=..., credentials=<qualified name of the function>)`.
  It never calls `repr()` on the function, because a callable object holding keys would print them.
- `__getstate__` drops `_client` (a botocore client cannot be pickled anyway); the function pickles as
  whatever it is. Tested later (hardening).
- `store_s3()` validates its arguments the way R's `datom_store_s3()` in `R/store.R` does: `bucket`
  and `region` non-empty strings, `prefix` a string or `None`, `credentials` callable. Region is checked
  with botocore's `validate_region_name()`. It makes no request and does not call the function (R3.3).

## The client: nothing ambient (R4)

Built in `_build_client(region, credentials)` from a fresh `botocore.session.Session()` with five
components replaced. Each one was shown necessary by removing it alone and watching a planted setting
get through (research notes, section 3.2):

| Replaced | Stops |
|---|---|
| Config store: every setting a constant at its default, with no config or credentials file and configured endpoints ignored | `AWS_*` env vars, `~/.aws/config`, `~/.aws/credentials`, `AWS_ENDPOINT_URL*`, profiles, FIPS/dual-stack switches |
| Data loader limited to botocore's built-in data | `~/.aws/models` overrides |
| Credential resolver with one provider: ours | env keys, files, `credential_process`, SSO, web identity, container and instance metadata |
| Token provider chain emptied | `AWS_BEARER_TOKEN_S3` |
| Plugin context set to `DISABLED` while the client is built | `BOTOCORE_EXPERIMENTAL__PLUGINS` |

Region is passed explicitly. Proxy variables and `REQUESTS_CA_BUNDLE` are still honoured (R4.3).

One guard on every request, a `before-send.s3` handler, refuses (as `StoreUnreachable`) any request
that is:
- not `GetObject`, `HeadObject` or `HeadBucket` (R1.4; this also catches botocore's own `CreateSession`
  for S3 Express buckets), or
- signed for a region other than the store's. botocore follows S3's "bucket is in another region"
  redirect by itself and would silently re-send to that region (verified); the message names the
  region S3 reported, so the fix is obvious.

botocore's handler hooks and component replacement are not documented as stable API. The planted-
settings tests are what would catch a botocore release that changes them.

## Credentials (R3)

- `_Provider` (a botocore `CredentialProvider`) returns `DeferredRefreshableCredentials`, so the
  caller's function is first called on the first request and again on botocore's timing (decision 3:
  from 15 minutes before expiry, forced in the last 10).
- Each result is checked by `_parse_credentials(result)`: a mapping; `access_key` and `secret_key`
  non-empty strings; `session_token` absent, `None` or a non-empty string; `expiration` absent, `None`,
  a timezone-aware `datetime` or an ISO-8601 string with an offset. Already expired is refused too
  (botocore would otherwise raise a bare `RuntimeError`). Messages name the field and the problem,
  never a value (R3.4).
- No expiry is passed to botocore as a far-future date, because botocore's refresh cannot take `None`.
- An exception from the function becomes a datom error chained to it (R3.5). The message does not
  include the exception's text, since a user function could have put a key there.

## Errors (R2)

Messages are built only from our own words, the bucket, the key, the S3 error code and the HTTP
status. botocore's exception text and response body are never copied in: they can carry
server-supplied text, and a real S3 signature error body can include the access key id. The datom
error is raised outside the `except` block, so the botocore exception is not attached to it either.

| S3 response | datom error |
|---|---|
| 404, `NoSuchKey` | `NotFound` |
| 404 on `HeadBucket`, `NoSuchBucket` | `StoreUnreachable` ("bucket does not exist") |
| 403, `AccessDenied`, `InvalidAccessKeyId`, `SignatureDoesNotMatch`, `ExpiredToken`, `InvalidToken` | `AccessDenied` (`access_denied`); on `HeadBucket` the message names the `s3:ListBucket` permission (decision 1) |
| Connection, timeout, TLS errors | `StoreUnreachable` (see Open decisions 2) |
| Anything else | `StoreUnreachable`, with the code and status |

**Debug logs.** With botocore's DEBUG logging on, botocore itself logs the access key id and the
session token (never the secret key). A `logging.Filter` on `botocore.endpoint`, `botocore.auth` and
`botocore.parsers` drops records mentioning the authorization header, the security-token header or the
credential scope. It is added once, when the first S3 client is built, and affects every botocore
client in the process (see Open decisions 3).

## Tests (preview)

All under moto's `mock_aws`, with `AWS_*` variables removed and `HOME` pointed at an empty temporary
folder by an autouse fixture, so no test can reach real AWS or local configuration (R8.2).

| Test | Covers |
|---|---|
| Every reader case uploaded to a fake bucket and run through `store_s3()`, with and without a prefix, same expectations and `SKIPPED` list as the local run | AC1 |
| The request guard records every operation across all cases; only the three allowed appear | AC2 |
| Missing object, denied object (moto IAM policy), missing bucket each raise their code | AC3 |
| Wrong keys, region and endpoint planted in env vars and in `~/.aws/` files: requests are signed with the function's key for the given region | AC4 (core) |
| The function is not called by `store_s3()`; with a fake clock moved near expiry, it is called again | AC6 |
| Each malformed credential result is refused; the message contains none of the planted values | AC7 |
| `repr()`, `vars()` and error messages after reads contain no planted secret; debug logs neither | AC8 (minus pickle) |
| In a fresh interpreter with boto3 and botocore blocked, `import datom` and a local read work; `store_s3()` says to install `datom[s3]` | AC10 |

Local environment: the repo's `.venv` is Python 3.9, below the package's 3.10 minimum, so no test
runs in it today. The first task recreates it on Python 3.10 or later.

## Open decisions

Each has a default the preview will use if not overruled.

1. **New error codes. DECIDED (owner, 2026-10-19):** `access_denied`, `credentials_invalid` (the
   function's result is malformed or expired), `credentials_failed` (the function raised),
   `extra_not_installed` (boto3 missing). While datom-py is 0.x these names may still change (see
   the error-code rule in `.kiro/steering/conventions.md`).
2. **A network failure when connecting. DECIDED (owner, 2026-10-19): stop at connect** with
   `StoreUnreachable`, as R2.3 says. This differs from R, which warns and carries on ("offline use")
   and fails at the first read. A datom-py reader has no local copy to fall back on, so it cannot do
   anything offline. Cheap to revisit while experimental.
3. **The debug-log filter affects the whole process. DECIDED (owner, 2026-10-19): accept it.** Any
   other boto3 client in the same program also loses those debug lines; the alternative is a session
   token in a log file whenever someone turns on debugging.
4. **A bucket in a different region from the one given. DECIDED (owner, 2026-10-19): refuse** with a message naming the
   bucket's region (the guard above). The alternative is to follow the redirect as botocore does, which
   breaks "the given region" in R4.2. Whether R's S3 library follows the redirect is not verified; if
   it does, Python would refuse a store R reads, and the user fixes it by passing the right region.
