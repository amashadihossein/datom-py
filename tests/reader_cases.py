"""The shared reader cases, fetched from R datom at a pinned commit.

The cases live in R datom, under ``tests/testthat/fixtures/reader-cases/``: small
stores R datom wrote, each with the result any reader must produce (see the
README there). Each case is ``case.json`` plus ``storage.zip``.

This module downloads that folder at :data:`DATOM_COMMIT` once, unpacks every
``storage.zip`` to ``storage/``, and caches the result under
``tests/.reader-cases/``. :data:`CASES_DIR` is the unpacked folder.

**The pin is the one place this package says which cases it must pass.** Bump it
on purpose, in its own commit, after the new cases pass.

To test against an R datom checkout instead (say, a case not yet merged there),
set ``DATOM_READER_CASES`` to its ``tests/testthat/fixtures/reader-cases`` folder.
"""
from __future__ import annotations

import io
import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

#: The R datom commit whose reader cases this package must pass.
DATOM_COMMIT = "557a30269ccd34c59f4d7a9daac9b1bd71bf8dd0"
DATOM_REPO = "amashadihossein/datom"
CASES_PATH = "tests/testthat/fixtures/reader-cases"

_CACHE = Path(__file__).parent / ".reader-cases"


def _download_cases(dest: Path) -> None:
    """Copy the pinned commit's reader-cases folder into ``dest``."""
    url = f"https://codeload.github.com/{DATOM_REPO}/tar.gz/{DATOM_COMMIT}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    prefix = f"{DATOM_REPO.split('/')[1]}-{DATOM_COMMIT}/{CASES_PATH}/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.startswith(prefix) and m.isfile()]
        if not members:
            raise RuntimeError(f"No reader cases at {CASES_PATH} in {DATOM_REPO}@{DATOM_COMMIT}.")
        for m in members:
            out = (dest / m.name[len(prefix):]).resolve()
            if dest.resolve() not in out.parents:
                raise RuntimeError(f"Archive member escapes the cases folder: {m.name!r}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tar.extractfile(m).read())


def _unpack(src: Path, dest: Path) -> None:
    """Each case's case.json, and its storage.zip unpacked to storage/."""
    for case in sorted(p for p in src.iterdir() if p.is_dir()):
        out = dest / case.name
        out.mkdir(parents=True)
        shutil.copy2(case / "case.json", out / "case.json")
        with zipfile.ZipFile(case / "storage.zip") as z:
            z.extractall(out / "storage")


def _materialise() -> Path:
    local = os.environ.get("DATOM_READER_CASES")
    if local:
        dest = Path(tempfile.mkdtemp(prefix="datom-reader-cases-"))
        _unpack(Path(local), dest)
        return dest

    # Short folder name: Windows paths inside a case run close to 260 characters.
    dest = _CACHE / DATOM_COMMIT[:12]
    if dest.is_dir():
        return dest
    _CACHE.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="fetch-", dir=_CACHE))
    try:
        _download_cases(work / "src")
        _unpack(work / "src", work / "cases")
        # A rename, so an interrupted fetch never leaves a half-filled cache.
        try:
            (work / "cases").rename(dest)
        except OSError:
            if not dest.is_dir():  # another test process got there first
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest


CASES_DIR = _materialise()
