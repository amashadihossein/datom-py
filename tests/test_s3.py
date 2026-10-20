"""S3 store (preview): amashadihossein/datom#129 part 2, spec ``.kiro/specs/s3-reader/``."""

from __future__ import annotations

import io
import logging
import os
import pickle
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import EndpointConnectionError
from moto import mock_aws

import datom
from datom import _s3
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


# --- the store against a fake S3 ----------------------------------------------------
#
# Every test below runs inside moto's mock_aws, so no request can reach real AWS.
# Canned responses are put in front of moto with register_first; the store's own
# guard still runs on every request.

FUNCTION_KEY = "AKIAFUNCTIONKEY00000"
FUNCTION_SECRET = "function-secret-value-0000"
FUNCTION_TOKEN = "function-session-token-0000"
SECRETS = (FUNCTION_KEY, FUNCTION_SECRET, FUNCTION_TOKEN)
BUCKET = "datom-test"


def function_credentials():
    return {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "session_token": FUNCTION_TOKEN}


@pytest.fixture(autouse=True)
def no_ambient_aws(monkeypatch, tmp_path):
    """No AWS environment variable and an empty home folder, for every test here."""
    for name in list(os.environ):
        if name.startswith(("AWS_", "BOTOCORE_", "BOTO_")):
            monkeypatch.delenv(name)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture
def fake_s3():
    """A moto S3 with one bucket holding ``proj/datom/dm/x.json``."""
    with mock_aws():
        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
        )
        client.create_bucket(Bucket=BUCKET)
        client.put_object(Bucket=BUCKET, Key="proj/datom/dm/x.json", Body=b'{"a": 1}')
        yield client


def make_store(credentials=function_credentials, **kwargs):
    kwargs.setdefault("prefix", "proj")
    return datom.store_s3(BUCKET, credentials=credentials, **kwargs)


class _Raw(io.BytesIO):
    def stream(self, **_):
        yield self.getvalue()


def answer_with(store, respond):
    """Put ``respond(request) -> (status, headers, body)`` in front of moto."""

    def handler(request, **_):
        status, headers, body = respond(request)
        return AWSResponse(request.url, status, headers, _Raw(body))

    store._get_client().meta.events.register_first("before-send.s3", handler)


def record_requests(store):
    sent = []

    def recorder(request, event_name, **_):
        sent.append((event_name.rsplit(".", 1)[-1], dict(request.headers)))

    store._get_client().meta.events.register("before-send.s3", recorder)
    return sent


def test_reads_objects_under_prefix_datom(fake_s3):
    store = make_store()
    conn = datom.get_conn(store, "STUDY_001")
    assert conn.store is store
    assert store.exists("dm/x.json")
    assert store.read_bytes("dm/x.json") == b'{"a": 1}'
    assert not store.exists("dm/missing.json")
    with pytest.raises(datom.NotFound) as e:
        store.read_bytes("dm/missing.json")
    assert e.value.code == "not_found"
    assert "s3://datom-test/proj/datom/dm/missing.json" in str(e.value)


def test_key_that_escapes_the_namespace_is_refused(fake_s3):
    with pytest.raises(datom.InvalidAddress):
        make_store().read_bytes("../x.json")


def test_missing_bucket_is_unreachable(fake_s3):
    store = datom.store_s3("no-such-bucket", credentials=function_credentials)
    with pytest.raises(datom.StoreUnreachable, match="does not exist"):
        datom.get_conn(store, "STUDY_001")


def test_access_denied_on_the_bucket_names_the_permission(fake_s3):
    store = make_store()
    answer_with(store, lambda r: (403, {}, b""))  # real S3 sends HEAD errors with no body
    with pytest.raises(datom.AccessDenied) as e:
        store.check_reachable()
    assert e.value.code == "access_denied"
    assert "s3:ListBucket" in str(e.value) and BUCKET in str(e.value)


def test_access_denied_on_an_object_names_bucket_and_key(fake_s3):
    store = make_store()
    body = b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"
    answer_with(store, lambda r: (403, {"Content-Type": "application/xml"}, body))
    with pytest.raises(datom.AccessDenied) as e:
        store.read_bytes("dm/x.json")
    assert "s3://datom-test/proj/datom/dm/x.json" in str(e.value)


def test_mapped_errors_carry_no_botocore_exception(fake_s3):
    store = make_store()
    with pytest.raises(datom.NotFound) as e:
        store.read_bytes("dm/missing.json")
    assert e.value.__cause__ is None and e.value.__context__ is None


def test_unreachable_endpoint(fake_s3, monkeypatch):
    monkeypatch.setattr("botocore.endpoint.time.sleep", lambda _: None)  # skip retry backoff
    store = make_store()

    def no_network(request, **_):
        raise EndpointConnectionError(endpoint_url=request.url)

    store._get_client().meta.events.register_first("before-send.s3", no_network)
    with pytest.raises(datom.StoreUnreachable, match="Could not reach"):
        store.check_reachable()


def test_bucket_in_another_region_is_refused(fake_s3):
    store = make_store()
    sent = record_requests(store)

    def redirect(request):
        if "eu-west-1" in request.url:
            return 200, {}, b""
        return 301, {"x-amz-bucket-region": "eu-west-1"}, b""

    answer_with(store, redirect)
    with pytest.raises(datom.StoreUnreachable, match="region='eu-west-1'"):
        store.check_reachable()
    assert len(sent) == 1  # the re-signed request for eu-west-1 never left


@pytest.mark.parametrize("call", ["list_objects_v2", "delete_object", "put_object"])
def test_only_read_operations_can_be_sent(fake_s3, call):
    client = make_store()._get_client()
    params = {"Bucket": BUCKET} if call == "list_objects_v2" else {"Bucket": BUCKET, "Key": "proj/datom/dm/x.json"}
    with pytest.raises(_s3._RequestRefused):
        getattr(client, call)(**params)
    assert fake_s3.get_object(Bucket=BUCKET, Key="proj/datom/dm/x.json")["Body"].read() == b'{"a": 1}'


def test_requests_are_signed_with_the_functions_credentials(fake_s3):
    store = make_store()
    sent = record_requests(store)
    store.read_bytes("dm/x.json")
    (operation, headers), = sent
    auth = headers["Authorization"]
    auth = auth.decode() if isinstance(auth, bytes) else auth
    assert operation == "GetObject"
    assert f"Credential={FUNCTION_KEY}/" in auth and "/us-east-1/s3/aws4_request" in auth
    token = headers["X-Amz-Security-Token"]
    assert (token.decode() if isinstance(token, bytes) else token) == FUNCTION_TOKEN


# --- credentials (R3) ---------------------------------------------------------------


def counting(expiration):
    calls = []

    def credentials():
        calls.append(1)
        return {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "expiration": expiration}

    return credentials, calls


def test_function_is_called_on_first_request_and_again_near_expiry(fake_s3, monkeypatch):
    start = datetime.now(timezone.utc)
    credentials, calls = counting(start + timedelta(hours=2))
    store = make_store(credentials)
    store._get_client()
    assert calls == []  # building the client does not ask for credentials
    for _ in range(3):
        store.read_bytes("dm/x.json")
    assert len(calls) == 1
    monkeypatch.setattr(_s3, "_now", lambda: start + timedelta(hours=2) - timedelta(minutes=10))
    store.read_bytes("dm/x.json")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "expiration",
    [None, datetime.now(timezone.utc) + timedelta(hours=1), "2999-01-01T00:00:00Z", "2999-01-01T00:00:00+02:00"],
)
def test_accepted_expirations(fake_s3, expiration):
    credentials, calls = counting(expiration)
    assert make_store(credentials).read_bytes("dm/x.json") == b'{"a": 1}'


@pytest.mark.parametrize(
    "result",
    [
        None,
        "AKIA-as-a-string",
        [FUNCTION_KEY, FUNCTION_SECRET],
        {},
        {"access_key": FUNCTION_KEY},
        {"secret_key": FUNCTION_SECRET},
        {"access_key": "", "secret_key": FUNCTION_SECRET},
        {"access_key": FUNCTION_KEY, "secret_key": 12345},
        {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "session_token": ""},
        {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "expiration": "next tuesday"},
        {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "expiration": 1700000000},
        {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "expiration": datetime(2999, 1, 1)},
        {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "expiration": "2000-01-01T00:00:00Z"},
    ],
)
def test_malformed_credentials_are_refused_without_echoing_a_value(fake_s3, result):
    store = make_store(lambda: result)
    with pytest.raises(datom.CredentialsInvalid) as e:
        store.read_bytes("dm/x.json")
    assert e.value.code == "credentials_invalid"
    for value in (*SECRETS, "AKIA-as-a-string", "12345", "next tuesday", "1700000000"):
        assert value not in str(e.value)


def test_function_that_raises_is_chained_but_not_echoed(fake_s3):
    def leaky():
        raise RuntimeError(f"keyring locked; key was {FUNCTION_KEY}")

    with pytest.raises(datom.CredentialsFailed) as e:
        make_store(leaky).read_bytes("dm/x.json")
    assert e.value.code == "credentials_failed"
    assert isinstance(e.value.__cause__, RuntimeError)
    assert FUNCTION_KEY not in str(e.value) and "leaky" in str(e.value)


# --- no secret where it can leak (R2.4, R5) -----------------------------------------


def test_debug_logs_carry_no_secret(fake_s3, caplog):
    caplog.set_level(logging.DEBUG, logger="botocore")
    make_store().read_bytes("dm/x.json")
    assert any(r.name.startswith("botocore") for r in caplog.records)  # logging did happen
    for secret in SECRETS:
        assert secret not in caplog.text


def test_store_and_conn_hold_no_secret_after_a_read(fake_s3):
    store = make_store()
    conn = datom.get_conn(store, "STUDY_001")
    datom_bytes = store.read_bytes("dm/x.json")
    assert datom_bytes
    assert set(vars(store)) == {"bucket", "prefix", "region", "_credentials", "_client"}
    assert vars(store)["_credentials"] is function_credentials
    for text in (repr(store), repr(conn), str(conn), repr(vars(store))):
        for secret in SECRETS:
            assert secret not in text


def test_pickled_conn_holds_no_secret_and_still_reads(fake_s3):
    conn = datom.get_conn(make_store(), "STUDY_001")
    conn.store.read_bytes("dm/x.json")
    raw = pickle.dumps(conn)
    for secret in SECRETS:
        assert secret.encode() not in raw
    again = pickle.loads(raw)
    assert again.store._client is None
    assert again.store.read_bytes("dm/x.json") == b'{"a": 1}'
