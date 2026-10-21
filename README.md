# datom for Python

A read-only Python reader for data written by [datom](https://github.com/amashadihossein/datom), the R package for versioned, traceable tabular data.

It reads tables, sets, version history and project listings from a datom store, and makes the same checks R datom makes before handing over data. It never writes to storage.

**Status: early development.** It reads local-folder stores, and S3 stores as a preview. Expect changes between 0.x releases; see the [changelog](https://github.com/amashadihossein/datom-py/blob/main/CHANGELOG.md). This is the Python reader planned in [amashadihossein/datom#129](https://github.com/amashadihossein/datom/issues/129). See [Not yet](#not-yet).

## Install

Python 3.10 or later.

```sh
pip install datom          # local-folder stores
pip install "datom[s3]"    # S3 stores too (adds boto3)
```

The import name is `datom`. To work on the package itself, clone the repository and `pip install -e ".[dev]"` (see [Tests](#tests)).

## Use

```python
import datom

store = datom.store_local("path/to/storage")   # the folder given to R's datom_store_local()
conn = datom.get_conn(store, project_name="STUDY_001")

df  = datom.read(conn, "dm")                       # current version, pandas
df  = datom.read(conn, "dm", version="a3f8c1d2")   # an older version, by a prefix of its id
tbl = datom.read(conn, "dm", as_arrow=True)        # a pyarrow Table

datom.history(conn, "dm")                          # versions, newest first
datom.list_artifacts(conn)                         # tables and sets (R: datom_list())
```

Integer and logical columns come back as pandas' nullable types (`Int32`, `Int64`, `boolean`), so an R `integer64` column keeps its exact values and missing values stay missing.

### Sets

A set is a list of pointers to tables (or other sets) at exact versions, plus labels. Reading one fetches no member data.

```python
s = datom.get_set(conn, "trial_product")           # or version="6d702efe"
s.members[0].id        # MemberId(project='STUDY_001', name='dm', kind='table', version='4151f1...')
s.members[0].tags      # {'role': ['baseline'], 'type': ['input']}

# Fetch one member's data. A name that matches two members (the same table at
# two versions is allowed) must be narrowed by a label or a version prefix.
dm = datom.fetch_member(conn, s, "dm", tags={"role": "current"})
dm = s.members[1].fetch(conn)
```

A member from another project needs a connection to that project.

### S3 stores (preview)

Everything above works the same on an S3 store. Instead of keys, you pass a **function** that returns them; datom calls it when it first needs credentials, and again before they expire.

```python
import keyring   # the OS keychain; any source you trust works

def my_credentials():
    return {
        "access_key": keyring.get_password("datom-s3", "access_key"),
        "secret_key": keyring.get_password("datom-s3", "secret_key"),
        # optional: "session_token": ..., "expiration": <aware datetime or ISO-8601 string>
    }

store = datom.store_s3("my-bucket", prefix="my-project", region="us-east-1", credentials=my_credentials)
conn = datom.get_conn(store, project_name="STUDY_001")
df = datom.read(conn, "dm")
```

With AWS single sign-on, `datom.sso_credentials()` makes the function for you. It runs the AWS CLI v2 (`aws configure export-credentials`) each time datom needs fresh credentials; run `aws sso login --profile my-profile` first.

```python
store = datom.store_s3("my-bucket", prefix="my-project", credentials=datom.sso_credentials("my-profile"))
```

Signing in with AWS single sign-on stores a temporary token, valid for a few hours, as plain text under `~/.aws/`. That is how AWS's own tools work, and datom cannot avoid it. Treat that folder as sensitive, and never copy or commit it.

`sso_credentials()` refuses credentials with no expiration, since that is what permanent plain-text keys produce. When the AWS CLI fails (not logged in, session expired), the error shows what the CLI said, never the keys it printed.

`bucket`, `prefix` and `region` mean what they mean in R's `datom_store_s3()`. Connecting checks the bucket, which needs the `s3:ListBucket` permission as in R.

The credential rules:

- **datom never looks for credentials.** Only your function's result is used. AWS environment variables, `~/.aws/` files, profiles, single sign-on caches and the machine's instance or container credentials are all ignored, and so are region and endpoint settings in any of those places.
- **datom never keeps or prints them.** The store holds your function, not its result. Keys never appear when a store or connection is printed, in error messages, or in boto3's debug logs. Pickling a store saves your function but not the keys it returned, so keep keys out of the function itself (fetch them inside it, as above).
- **Only reads are sent**: S3's `GetObject`, `HeadObject` and `HeadBucket`. Anything else is refused before it leaves.
- A bucket in a different region from the one you pass is refused, with a message naming the bucket's region.

Errors: a missing object is `not_found`; denied access is `access_denied`; a missing bucket or no network is `store_unreachable`; a bad result from your function is `credentials_invalid`, and an exception from it is `credentials_failed`.

## What it checks

The same rules as R datom, in the same order:

1. **Format.** A metadata or manifest document declaring a newer format than this package knows (`schema_version` above 2) is refused, as is a malformed format number.
2. **Kind.** A set read with `read()`, or a table read with `get_set()`, is refused before the data path is built.
3. **Version.** No version means the current one. Otherwise the version is matched as a prefix of the ids in the history; no match or two matches is refused.
4. **Data address.** The address in the metadata must be 6 to 64 lowercase hex characters before it goes anywhere near a file path.
5. **Download, hash, then parse.** The SHA-256 of the downloaded bytes must match the recorded hash before anything is parsed. A table written before datom recorded hashes skips this check; a set without a hash is refused.
6. **Sets.** Members come back as pointers; malformed member records are refused.
7. **Older manifests** are converted in memory, never written back.
8. **A governance marker** in storage triggers a warning that the location you supplied may go stale after a data move, then the read carries on, as in R.

Every refusal is a `datom.DatomError` subclass with a `code`, such as `integrity_mismatch` or `version_ambiguous`. While this package is experimental (0.x), a code's name may still change; its meaning will not.

## Tests

```sh
pip install -e ".[dev]"
pytest
```

The tests run against **shared reader cases**: small datom stores written by R datom, each saved with the result any reader must produce. Python must read what R reads and refuse what R refuses, and R datom's own test suite runs the same cases. Each case runs against a local folder and against a fake S3 bucket ([moto](https://github.com/getmoto/moto)), with and without a prefix; no test touches real AWS or needs any AWS configuration. They live in R datom, under [`tests/testthat/fixtures/reader-cases/`](https://github.com/amashadihossein/datom/tree/dev/tests/testthat/fixtures/reader-cases) (its README describes a case and the error names). Unit tests in `tests/test_rules.py` cover each rule above.

The first `pytest` run downloads the cases from the R datom commit pinned in [`tests/reader_cases.py`](https://github.com/amashadihossein/datom-py/blob/main/tests/reader_cases.py) and caches them under `tests/.reader-cases/`. The pin is bumped on purpose, in its own commit. To test against a local R datom checkout instead, set `DATOM_READER_CASES` to its `tests/testthat/fixtures/reader-cases` folder.

GitHub Actions runs the tests on Python 3.10 to 3.13 (Linux), and on Windows and macOS.

To show the tests catch a broken rule, `python tools/check_tests_catch_breaks.py` breaks each rule in turn (20 breaks) and confirms the suite fails every time.

## Not yet

- **S3 hardening**: more tests of the credential rules. S3 stores work today as a preview.
- S3-compatible stores on other endpoints (MinIO and similar); R datom has no endpoint option either.
- Finding a project's data location through its governance repo.
- Rebuilding a manifest whose artifact list can't be read. This package stops with a message where R datom would rebuild.
- Lineage helpers and diffs.
- Writing anything. That needs datom's table hashing reproduced byte for byte.

Things found while building the reader that affect R datom or the plan in #129 are listed in [FINDINGS.md](https://github.com/amashadihossein/datom-py/blob/main/FINDINGS.md).

## License

MIT
