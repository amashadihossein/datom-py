# Changelog

datom for Python is experimental while its version starts with 0. Error `code` names may still
change between 0.x releases; when one does, it is listed here. A code never changes meaning.

## 0.1.0

The first release: a read-only reader for data written by R datom
([amashadihossein/datom#129](https://github.com/amashadihossein/datom/issues/129)).

- **Reads** tables, sets, version history and project listings, with the same checks R datom makes
  before handing over data: format, kind, version, data address, and the hash of the bytes before
  anything is parsed. Every shared reader case from R datom passes.
- **Local-folder stores**: `datom.store_local()`.
- **S3 stores (preview)**: `datom.store_s3()`, with `pip install "datom[s3]"`. Credentials come only
  from a function you pass. datom never reads AWS environment variables, `~/.aws/` files or instance
  credentials, never keeps or prints keys, and sends only read requests. A bucket in another region
  from the one given is refused.
- **Single sign-on**: `datom.sso_credentials(profile)` makes the credential function from an AWS SSO
  profile through the AWS CLI v2.
- **Errors** are `datom.DatomError` subclasses with a `code`. New in this release, for S3:
  `access_denied`, `credentials_invalid`, `credentials_failed`, `extra_not_installed`.

Not yet: finding a project's data location through its governance repo, S3-compatible endpoints,
lineage helpers, and writing anything. See the README.
