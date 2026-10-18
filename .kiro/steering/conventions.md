---
inclusion: always
---
# datom-py -- conventions

`datom` for Python: a **read-only** reader for data written by R datom. PyPI and import name
`datom`; Python 3.10 or later. The plan it implements is
[amashadihossein/datom#129](https://github.com/amashadihossein/datom/issues/129).

## R datom is the reference

- When Python and R behave differently, **R is right** unless #129 or a spec here says otherwise.
- Every read rule is a port of an R function. Cite it by name in the docstring
  (`.datom_resolve_version()` in `R/read_write.R`), never by line number: lines move.
- The **shared reader cases** are the contract. They live in R datom
  (`tests/testthat/fixtures/reader-cases/`, whose README is the written read contract) and are fetched
  from the commit pinned in `tests/reader_cases.py`. Bump the pin on purpose, in its own commit.
- A case this package cannot pass yet goes in `SKIPPED` in `tests/test_reader_cases.py` with the
  reason. **Never skip silently.**

## Working beside R datom (one workspace, two repos)

Both repos are open in one workspace, so either is in view when it helps. They stay separate:

- **A change touches one repo.** Work that needs both is two issues and two PRs: the R change merges
  first, then the Python side bumps its pin. Never edit one repo as part of the other's task.
- **The pin is the only link.** Python tests never read the R checkout on disk, except when
  `DATOM_READER_CASES` is set on purpose to try an unmerged case.
- **Each repo keeps its own rules.** Inside `datom/`, R datom's `.github/copilot-instructions.md` and
  its `.kiro/steering/` apply; inside `datom-py/`, this file does. Branches, PRs and issues belong to
  the repo whose files change.
- Before a git command, check which repo it runs in (`git -C <repo>` or the right working
  directory). Both repos have branches with similar names.

## Rigor matches risk: name the tier before starting

Classify by the worst thing a mistake could do, not by the size of the diff. Same tiers as R datom
("Rigor matches risk" in its `.github/copilot-instructions.md`):

| Tier | Worst case of a mistake | Examples here |
|---|---|---|
| 0 Trivial | A reader is confused | Typo, wording, README, comment |
| 1 Small | One feature misbehaves, loudly | A message, a new test, a fix in one module |
| 2 Standard | Several features misbehave, or a user-visible behaviour changes | A new public function; a change across modules |
| 3 Foundational | Silent damage: wrong data, a check that stops checking, a leaked secret | The format, kind, version, data-address or hash checks; storage routing; **credential handling**; releasing to PyPI |

| | 0-1 | 2 | 3 |
|---|---|---|---|
| Process | Branch, commit, PR | Same, with a short written plan | A spec under `.kiro/specs/{feature}/`: requirements, design, tasks |
| Stops | One, at the PR | Once on the plan, once at the PR | After requirements, design, tasks, and every task |
| Decisions | Batched, each with a default | Batched, each with a default | One at a time |
| Tests | Full suite once before the PR | Same | Full suite before every commit |

Escalate the moment the evidence changes: if Tier 1-2 work turns out to touch a Tier 3 row, stop,
say so, and ask.

## Stops and approvals

- A question from the owner is answered, not acted on. Approval is explicit: "go ahead", "yes",
  "approve", or similar, aimed at the next step.
- At a Tier 3 checkpoint, post what shipped and ask before the next task, even if it looks obvious.

## Tests

- `pip install -e ".[dev]"`, then `pytest`. Report the count in the commit message.
- **A guard's test must go red when that guard alone is removed.** Probe each new guard once,
  against its own test, with a do-nothing control first, and record the result in the commit
  message. A probe that reddens nothing is ambiguous until you confirm the edit changed behaviour.
  Where a break can be expressed in `tools/check_tests_catch_breaks.py`, add it there.
- No test touches real AWS or needs any AWS configuration.

## Credentials (not negotiable)

datom receives secrets explicitly at runtime and never discovers, persists or prints them.

- Credentials enter only through a function the caller passes in. Never read environment variables,
  `~/.aws/`, instance metadata or any credential file implicitly, and never let boto3 fall back to its
  default lookup.
- Store and connection objects hold the function, never its result. No key appears in `repr()`,
  errors, warnings, logs or pickles.
- No secret in code, docs examples, tests, committed files or commit messages.

## Code

- Public API is re-exported from `src/datom/__init__.py`; implementation lives in private `_*.py`
  modules.
- Every refusal is a `DatomError` subclass with a stable `code` (`_errors.py`). **A released code is
  never renamed or repurposed**; add a new one beside it instead.
- The package never writes to storage. A store has read methods only.
- Required dependencies: `pyarrow>=20`, `pandas>=2.0`. Anything else is an optional extra.
- Small functions, early returns, type hints, no clever metaprogramming.

## Git

- Branch off `main`, PR into `main`, never push to `main`. Commit only when asked or when the task
  workflow says to.
- Multi-line commit messages and PR bodies go through a file (`git commit -F`, `--body-file`).
- Before retrying anything that changes a remote (push, PR, issue), check whether the first attempt
  already landed.
