# Requirements -- S3 reader

**Issue**: [amashadihossein/datom#129](https://github.com/amashadihossein/datom/issues/129), part 2
("S3 reads with the credential function; single sign-on helper"; "every shared case passes, local and
fake S3").
**Branch**: `spec/s3-reader`, cut from `main` at `14c49c0`. PRs into `main`.
**Tier**: 3 (credential handling). Requirements, design and tasks each stop for the owner's approval.
**Test baseline**: 107 passed, 20 of 20 breaks caught (`tools/check_tests_catch_breaks.py`).

## Goal

Read a datom project from an S3 bucket with exactly the checks the local-folder reader already makes,
while holding to datom's credential rule: **datom receives secrets explicitly at runtime and never
discovers, persists or prints them.** For the Python reader that means credentials arrive only through
a function the caller passes in, and nothing ambient (environment variables, `~/.aws/`, instance
metadata) can change which credentials, which region or which endpoint a request uses.

## Requirements

### R1. An S3 store

1. `datom.store_s3(bucket, prefix=None, region="us-east-1", *, credentials)` returns a read-only
   store. `bucket`, `prefix` and `region` mean what they mean in R's `datom_store_s3()`; `credentials`
   replaces R's `access_key` / `secret_key` / `session_token` (R3).
2. Object keys are `{prefix}/datom/{key}`, built by the same function the local store uses
   (`build_storage_key()` in `_store.py`), so both backends address a project identically.
3. The store offers the three operations the reader already uses on a local store -- check it is
   reachable, test whether an object exists, read an object's bytes -- and **nothing else**. No
   write, delete, copy or list operation exists on it.
4. The only S3 requests the store ever makes are reads: `GetObject`, `HeadObject`, and `HeadBucket`
   once when connecting (decision 1).
5. Every read rule (format check, kind check, version resolution, data-address check, hash before
   parse, set rules, older manifests, governance warning) runs unchanged on an S3 store. No rule is
   reimplemented per backend.

### R2. Errors, with no secret in any of them

1. An object that does not exist raises the same `NotFound` the local store raises.
2. Credentials rejected, or access denied to an object or the bucket, raise a distinct error with its
   own stable `code`, `access_denied` (decision 2), naming the bucket and the key.
3. A bucket that does not exist, or an endpoint that cannot be reached, raises `StoreUnreachable`.
4. **No error message, warning, `repr()` or log line contains an access key, secret key or session
   token**, including errors raised while calling the credential function or parsing its result.

### R3. Credentials arrive only through a function

1. `credentials` is a callable taking no arguments and returning a mapping with `access_key`,
   `secret_key`, and optionally `session_token` and `expiration`.
2. `expiration` is a timezone-aware `datetime` or an ISO-8601 string. Absent or `None` means the
   credentials do not expire (static keys, for example from the OS keychain).
3. The function is first called when the store first needs credentials, not when it is constructed,
   and called again before credentials expire. A request is never sent with credentials past their
   `expiration`.
4. A result that is not a mapping, lacks `access_key` or `secret_key`, has an empty one, or has an
   unparseable `expiration` is refused with a clear message that names the problem and no value.
5. An exception raised by the credential function propagates as a datom error whose message does not
   include the function's output, chained to the original exception.

### R4. Nothing ambient changes a request

1. Which credentials, which region and which endpoint a request uses depend **only** on the arguments
   to `store_s3()`. None of these may influence them:
   - environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`,
     `AWS_PROFILE`, `AWS_DEFAULT_REGION`, `AWS_REGION`, `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_S3`,
     `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, and any other botocore reads);
   - `~/.aws/config` and `~/.aws/credentials`;
   - instance or container metadata endpoints;
   - `credential_process`, SSO or web-identity settings.
2. This holds with **no** credentials resolvable anywhere else (a clean machine) and with **wrong**
   ones planted in every place listed in 1: the store uses the function's credentials, the given
   region, and the default AWS endpoint for that region.
3. Network settings that do not affect identity or destination (proxy variables, CA bundle) are
   outside R4 and may be honoured.

### R5. No secret is held where it can leak

1. Store and connection objects hold the credential **function**, never its result, as anything a
   caller can reach: attributes, `repr()`, `str()`, `vars()`.
2. Pickling a store or a connection after a successful read writes no access key, secret key or
   session token into the pickle bytes. (A store whose credential function cannot be pickled may
   refuse to pickle; it must not pickle the resolved credentials instead.)
3. Credentials are kept only in memory, inside the S3 client, for the life of the store.

### R6. A single sign-on helper

1. `datom.sso_credentials(profile)` returns a credential function for an AWS SSO profile. Calling it
   runs `aws configure export-credentials --profile <profile> --format process` and returns the
   result in R3's shape. The user must have run `aws sso login` first; the helper does not log in.
2. The command runs as an argument list, never through a shell. `profile` must be a single non-empty
   string of the characters AWS allows in a profile name.
3. Output with no `Expiration` is refused, because that is what permanent plain-text keys produce,
   and the refusal message says so.
4. A failed command (AWS CLI missing, not logged in, session expired) raises a datom error carrying
   the command's error output. **Its standard output is never included**, since that is where the
   keys are.
5. Running the AWS CLI reads `~/.aws/` by design. That is the user's explicit choice of credential
   source, and is consistent with R4: R4 forbids datom reading it implicitly.

### R7. Dependencies

1. `boto3` is an optional extra: `pip install "datom[s3]"`. `import datom` and every local-folder read
   work without it.
2. `store_s3()` without boto3 installed raises a clear error telling the user to install `datom[s3]`.
3. Fake-S3 testing (`moto`) is a development dependency only.

### R8. Tests

1. **Every shared reader case passes against a fake S3**: each case's storage is uploaded to a fake
   bucket and read through `store_s3()`, both with and without a `prefix`. The same expected results,
   the same skip list (none today).
2. No test uses real AWS, and the suite passes on a machine with no AWS configuration at all.
3. Each guarantee in R2-R6 has a test, and each such test is shown failing when the behaviour it
   names is removed alone, recorded in the commit that adds it. New breaks join
   `tools/check_tests_catch_breaks.py` where they can be expressed there.
4. The local-folder suite is unchanged and still passes.

### R9. Documentation

1. README: installing the S3 extra; an S3 example using a credential function; the SSO helper; the
   credential rules in plain words (what datom never reads, what it never stores).
2. The caution, verbatim from #129: "Signing in with AWS single sign-on stores a temporary token,
   valid for a few hours, as plain text under `~/.aws/`. That is how AWS's own tools work, and datom
   cannot avoid it. Treat that folder as sensitive, and never copy or commit it."
3. For static keys, recommend the OS keychain (`keyring`), with an example credential function that
   reads from it. Never show secrets in code or files.
4. "Not yet" drops S3.

## Out of scope

- S3-compatible stores on other endpoints (MinIO and similar). R's `datom_store_s3()` has no endpoint
  argument either; adding one is a separate change in both languages.
- Finding a project's data location through its governance repo.
- Writing anything.
- The PyPI release. It follows this spec as its own step, once the S3 reader is merged.

## Decisions

1. **Check the bucket when connecting? DECIDED (owner, 2026-10-17): match R.** R checks the bucket
   with `HeadBucket` when an S3 store is built. That request needs the `s3:ListBucket` permission,
   which a reader granted only `GetObject` on one project's prefix may lack. `get_conn()` checks the
   bucket and turns a denial into the R2.2 error naming the missing permission. Revisit in both
   languages together if a narrowly scoped reader needs it.
2. **The name of the access-denied error code. DECIDED (owner, 2026-10-17): `access_denied`**, AWS's
   own term. A broader code can be added beside it later. (Renaming follows the stage-dependent
   error-code rule in `.kiro/steering/conventions.md`, amended 2026-10-19.)
3. **When credentials are refreshed. DECIDED (owner, 2026-10-17): botocore's own timing**, with no
   margin of ours. Its refreshable credentials call the function again about 15 minutes before
   expiry and force a refresh in the last 10. AC6's test, that the function is called again as
   expiry nears, is what would catch a botocore change that broke this.

## Acceptance criteria

| ID | Criterion | Requirement |
|---|---|---|
| AC1 | Every shared reader case passes through `store_s3()` on a fake S3, with and without a prefix | R1.5, R8.1 |
| AC2 | The store makes no request other than `GetObject`, `HeadObject` and `HeadBucket` across every case | R1.3, R1.4 |
| AC3 | Missing object, access denied and missing bucket each raise their own error code | R2.1-R2.3 |
| AC4 | With wrong credentials, region and endpoint planted in every ambient source, reads use only the function's credentials and the given region | R4.1, R4.2 |
| AC5 | With no AWS configuration anywhere, the suite passes | R4.2, R8.2 |
| AC6 | The credential function is not called at construction, and is called again as credentials near expiry | R3.3, decision 3 |
| AC7 | Each malformed credential result is refused without echoing a value | R3.4, R2.4 |
| AC8 | No secret appears in `repr()`, `vars()`, error messages, or pickle bytes of a store or connection after a read | R2.4, R5 |
| AC9 | The SSO helper refuses output without `Expiration`, never runs a shell, and never includes the command's standard output in an error | R6.2-R6.4 |
| AC10 | `import datom` and local reads work without boto3; `store_s3()` then says to install `datom[s3]` | R7.1, R7.2 |
| AC11 | Each test for AC2-AC10 is recorded going red when its behaviour alone is removed | R8.3 |
