# Shared reader cases

Small datom stores written by R datom, each saved with the result any reader must produce. Python's test suite runs every one (`tests/test_reader_cases.py`), and so can R (`Rscript tools/reader_cases.R check`).

This folder is a temporary copy of part 1 of [amashadihossein/datom#129](https://github.com/amashadihossein/datom/issues/129). The cases move to the R repo, under `tests/testthat/fixtures/reader-cases/`, and this package then fetches them from a pinned commit of that repo.

Generated with datom 0.2.0 (R), at commit `6da9a64` of amashadihossein/datom.

## What a case is

```
<case-name>/
  storage/     a datom store exactly as R datom wrote it (and edited, for refusal cases)
  case.json    what to call and the expected result
```

`storage/` holds only the artifacts the call touches, plus the project manifest, to keep the cases small (about 300 KB in total).

`case.json`:

| Field | Meaning |
|---|---|
| `description` | What the case is about |
| `generated_with` | The datom version that wrote it |
| `call` | What to run: `op` plus its arguments |
| `expect` | What R datom returned on this storage |
| `edited` | For cases made by editing the written files: what was changed |

Operations (`call.op`) and their arguments:

| `op` | Arguments | Python | R |
|---|---|---|---|
| `read_table` | `name`, `version` | `datom.read()` | `datom_read()` |
| `read_set` | `name`, `version` | `datom.get_set()` | `datom_get_set()` |
| `fetch_member` | `set`, `set_version`, `member`, `tags`, `version` | `datom.fetch_member()` | `datom_fetch_member()` |
| `history` | `name`, `n` | `datom.history()` | `datom_history()` |
| `list` | none | `datom.list_artifacts()` | `datom_list()` |

The project name is always `STUDY_001`. `version: null` means the current version.

A successful table read expects `version` (the full recorded id), `data_sha` (which data file was chosen), `nrow`, `ncol`, `colnames` and `arrow_types`. Types are compared by name rather than values: both languages read the same file bytes, and the hash check proves they are the same bytes. Type names are as R arrow prints them; see [FINDINGS.md](../../FINDINGS.md) for the two places pyarrow prints them differently.

A refusal expects `{"ok": false, "error": "<name>"}`, plus `r_class`, the R condition class R datom raised (for information).

## Never edited once committed

A committed case is never regenerated or edited. New behaviour gets a new case. This keeps every future version of both readers honest about every old store, and matches the rule in R datom's `tests/testthat/fixtures/README.md`.

## Error names

| Case error | Python | R condition class today |
|---|---|---|
| `schema_unsupported` | `SchemaUnsupported` | `datom_schema_unsupported` |
| `schema_invalid` | `SchemaInvalid` | `datom_schema_invalid` |
| `kind_mismatch` | `KindMismatch` | `datom_artifact_kind_conflict` |
| `integrity_mismatch` | `IntegrityMismatch` | `datom_set_integrity_failure` for sets; none for tables (`rlang_error`) |
| `document_sha_missing` | `DocumentShaMissing` | `datom_set_document_sha_missing` |
| `set_member_malformed` | `SetMemberMalformed` | `datom_set_member_malformed`, but it surfaces wrapped as `purrr_error_indexed` |
| `member_ambiguous` | `MemberAmbiguous` | `datom_member_ambiguous` |
| `member_not_found` | `MemberNotFound` | `datom_member_not_found` |
| `version_not_found` | `VersionNotFound` | none (`rlang_error`) |
| `version_ambiguous` | `VersionAmbiguous` | none (`rlang_error`) |
| `invalid_address` | `InvalidAddress` | none (`rlang_error`) |

Warnings: `governance_attached` (`GovernanceAttachedWarning` in Python; a plain warning in R, matched by its message).

## Adding a case

1. Add a `make_case(...)` call to `tools/reader_cases.R`.
2. Run `Rscript tools/reader_cases.R generate`. It adds the new case and leaves the others alone.
3. Run `pytest`. If Python can't handle the case yet, list it in `SKIPPED` in `tests/test_reader_cases.py` with the reason. Never skip silently.

## Where the read rules come from

The route cards in R datom's `dev/datom_pathways.md`: "Given table + version, read data", "Given a repo, decide whether this build can read it", and "Given a set + version, resolve its members".
