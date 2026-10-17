# Findings for amashadihossein/datom#129

Things found while building the first version of this reader that affect R datom or the plan in the issue.

## The shared-test plan

1. **Arrow type names don't compare exactly across languages.** The issue assumes `$ToString()` in R and `str()` in Python print the same name. They do for most types, with two exceptions:
   - **Factor (dictionary) columns.** R arrow prints `dictionary<values=string, indices=int8>`; pyarrow prints `dictionary<values=string, indices=int8, ordered=0>`. R arrow drops `ordered=0` and spells `ordered=1` as `ordered`. The Python test applies the same rewrite before comparing.
   - **Labelled columns.** R arrow stores `haven_labelled` (and other vctrs types) as an R-only extension type. Its R name isn't stable: it changed depending on whether `haven` was loaded. Outside R, the column is its storage type (`double`). The generator records extension types by their storage type.

2. **pyarrow before 20 reads factor columns differently.** pyarrow 14 to 19 return 32-bit dictionary indices where the file stores 8-bit ones. The values are the same, but the type names differ, so this package requires pyarrow 20 or later.

3. **Factor labels and value labels survive only in R.** Factor levels come through (as a pandas `category`), but `labelled` value labels live in R-specific schema metadata that Python can't read. This is worth a sentence in the Python docs.

4. **The default pandas conversion loses `integer64` precision.** An integer column with a missing value becomes `float64`, which rounds 9007199254740993 to 9007199254740992. This package uses pandas' nullable integer types instead. Any other non-R reader of datom data has the same trap.

## R datom

5. **The malformed-member refusal loses its class.** `.datom_read_set_member()` raises `datom_set_member_malformed` inside `purrr::walk()`, which re-signals it as `purrr_error_indexed`. A caller testing `inherits(e, "datom_set_member_malformed")` gets `FALSE`. The comment in `.datom_read_set_members()` already explains why that function uses `lapply()` rather than purrr; the inner `purrr::walk()` over the id fields has the same problem.

6. **Read errors without a stable class** are as the issue lists: table integrity mismatch, version not found, version ambiguous, and invalid address all surface as `rlang_error`. Each case records the R class it got, so this table can be checked when the classes are added.

7. **`datom_store_local()` creates a missing folder** even for a reader. The Python reader refuses instead, since it never writes. Probably harmless in R, but a reader-role store could skip the creation.

## Generating the cases

8. **The generator runs offline, end to end.** It uses `datom_init_repo()` with `mode = "product"`, a placeholder token and a local bare remote, then `datom_get_conn(store = ..., project_name = ...)` as the reader. No connection is hand-built. It takes a few seconds.

9. **Size.** 28 cases, about 300 KB in total, which is well under the issue's 1 MB. Each case's storage holds only the artifacts its call touches.
