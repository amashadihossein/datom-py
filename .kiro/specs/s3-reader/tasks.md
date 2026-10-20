# Tasks -- S3 reader

Design: `design.md`. One task = one commit; the full suite runs before every commit and its count goes
in the message. Tasks 1-4 are the preview; 5-10 harden the same code.

## Preview

- [x] **1. Packaging, error codes, lazy import.** Recreate the local `.venv` on Python 3.10 or later
  and confirm the 107-test baseline. Add the `s3` extra (`boto3>=1.40`) and `moto[s3]>=5` to `dev`.
  Add `AccessDenied`, `CredentialsInvalid`, `CredentialsFailed`, `ExtraNotInstalled` to `_errors.py`.
  Add `_s3.py` with `store_s3()` argument checks and the "install `datom[s3]`" refusal; export from
  `__init__.py`. Test: AC10 in a fresh interpreter with boto3/botocore blocked.
- [x] **2. The store and its client.** `S3Store` (three read methods, key check, `repr`, drop the
  client on pickle), `_build_client()` with the five replaced components, the credential provider and
  `_parse_credentials()`, the request guard (operation allow-list, signing region), the error mapping,
  the debug-log filter. Tests: AC3, AC6, AC7, AC8 without pickle, and the guard refusing a redirected
  region and a disallowed operation.
- [ ] **3. Reader cases on fake S3, and ambient settings.** Autouse fixture removing `AWS_*` variables
  and pointing `HOME` at an empty folder. Every reader case uploaded and run through `store_s3()`, with
  and without a prefix (AC1), recording operations (AC2). Wrong keys, region and endpoint planted in
  env vars and `~/.aws/` files, requests checked for the function's key and the given region (AC4
  core). The local-folder suite unchanged (R8.4).
- [ ] **4. README for the preview, then the PR.** Install the extra; an S3 example with a credential
  function; what datom never reads and never stores, in plain words; mark S3 as a preview in
  "Not yet". PR into `main`.

## Hardening

- [ ] **5. Single sign-on helper** `sso_credentials(profile)` (R6, AC9).
- [ ] **6. Every ambient source, one at a time**: one planted setting per test, each shown to get
  through when its replaced component is removed (full AC4, research notes section 3.2).
- [ ] **7. Pickling**: no secret in the pickle bytes of a store or connection after a read (AC8).
  The basic case (a connection pickled after a read) landed with task 2; what remains is a
  credential function that cannot be pickled, and one that carries keys inside itself.
- [ ] **8. Guard probes**: each check's test shown going red with that check alone removed, added to
  `tools/check_tests_catch_breaks.py` where it fits (AC11).
- [ ] **9. README credential guidance**: the single sign-on caution verbatim from #129, the keyring
  example (R9.2, R9.3).
- [ ] **10. CI on the newest botocore**, since the client builder relies on botocore internals.
