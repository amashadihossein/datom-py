"""S3 store (preview): amashadihossein/datom#129 part 2, spec ``.kiro/specs/s3-reader/``."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import datom
from tests.reader_cases import CASES_DIR

ROOT = Path(__file__).resolve().parents[1]  # the repo root, so the subprocess imports this checkout


def static_credentials():
    return {"access_key": "AKIAPLANTEDKEY000000", "secret_key": "planted-secret"}


def run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --- packaging: boto3 is optional (R7, AC10) ---------------------------------------


def test_import_datom_does_not_load_botocore():
    result = run_python(
        """
        import sys
        import datom
        print(sorted(m for m in ("boto3", "botocore") if m in sys.modules))
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_without_boto3_local_reads_work_and_store_s3_says_what_to_install():
    storage = CASES_DIR / "table-current" / "storage"
    result = run_python(
        f"""
        import sys
        sys.modules["boto3"] = None      # makes `import boto3` raise ImportError
        sys.modules["botocore"] = None
        import datom
        conn = datom.get_conn(datom.store_local({str(storage)!r}), "STUDY_001")
        print(len(datom.read(conn, "dm")))
        try:
            datom.store_s3("bucket", credentials=lambda: {{}})
        except datom.ExtraNotInstalled as e:
            print(e.code)
            print(e)
        """
    )
    assert result.returncode == 0, result.stderr
    nrow, code, message = result.stdout.strip().splitlines()
    assert nrow == "6"
    assert code == "extra_not_installed"
    assert 'pip install "datom[s3]"' in message


def test_too_old_botocore_is_refused(monkeypatch):
    import botocore

    monkeypatch.setattr(botocore, "__version__", "1.39.9")
    with pytest.raises(datom.ExtraNotInstalled, match="1.40 or later"):
        datom.store_s3("bucket", credentials=static_credentials)


# --- store_s3() arguments (R1.1, R3.3) ----------------------------------------------


def test_store_s3_keeps_its_arguments_and_does_not_call_the_function():
    calls = []

    def credentials():
        calls.append(1)
        return static_credentials()

    store = datom.store_s3("bucket", prefix="proj", region="eu-west-1", credentials=credentials)
    assert (store.bucket, store.prefix, store.region) == ("bucket", "proj", "eu-west-1")
    assert store.backend == "s3"
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bucket": ""},
        {"bucket": None},
        {"prefix": 3},
        {"region": ""},
        {"region": "not a region!"},
        {"credentials": "AKIA..."},
        {"credentials": None},
    ],
)
def test_store_s3_refuses_bad_arguments(kwargs):
    args = {"bucket": "bucket", "prefix": None, "region": "us-east-1", "credentials": static_credentials}
    args.update(kwargs)
    with pytest.raises(datom.InvalidArgument):
        datom.store_s3(args.pop("bucket"), args.pop("prefix"), args.pop("region"), **args)


def test_credentials_are_keyword_only():
    with pytest.raises(TypeError):
        datom.store_s3("bucket", None, "us-east-1", static_credentials)


# --- no secret held where it can leak (R5.1) ----------------------------------------


def test_repr_names_the_function_without_calling_its_repr():
    class KeyHolder:
        def __call__(self):
            return static_credentials()

        def __repr__(self):  # a careless user object that prints its keys
            return "KeyHolder(secret='planted-secret')"

    store = datom.store_s3("bucket", credentials=KeyHolder())
    text = repr(store)
    assert "planted-secret" not in text
    assert "KeyHolder" in text and "bucket" in text
    assert "static_credentials" in repr(datom.store_s3("bucket", credentials=static_credentials))
