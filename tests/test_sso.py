"""The single sign-on credential helper (R6, AC9).

The AWS CLI is replaced by a small Python script, so these tests need neither
the CLI nor any AWS configuration.
"""

from __future__ import annotations

import pickle
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

import datom
from datom import _sso

CLI_KEY = "AKIAFROMTHEAWSCLI000"
CLI_SECRET = "secret-from-the-aws-cli"
CLI_TOKEN = "token-from-the-aws-cli"
CLI_SECRETS = (CLI_KEY, CLI_SECRET, CLI_TOKEN)

FAKE_CLI = textwrap.dedent(
    f"""
    import json, os, sys, time
    with open(os.environ["DATOM_FAKE_AWS_LOG"], "a") as log:
        log.write(json.dumps(sys.argv[1:]) + "\\n")
    mode = os.environ["DATOM_FAKE_AWS_MODE"]
    creds = {{"Version": 1, "AccessKeyId": {CLI_KEY!r}, "SecretAccessKey": {CLI_SECRET!r},
             "SessionToken": {CLI_TOKEN!r}, "Expiration": os.environ.get("DATOM_FAKE_AWS_EXPIRY", "")}}
    if mode == "ok":
        print(json.dumps(creds, indent=2))
    elif mode == "no-expiration":
        del creds["Expiration"], creds["SessionToken"]
        print(json.dumps(creds, indent=2))
    elif mode == "fail":
        print(json.dumps(creds))  # keys on stdout must never reach an error
        print("Error loading SSO Token: Token for dev does not exist", file=sys.stderr)
        sys.exit(255)
    elif mode == "not-json":
        print("AccessKeyId={CLI_KEY} SecretAccessKey={CLI_SECRET}")
    elif mode == "slow":
        print(json.dumps(creds), flush=True)
        time.sleep(30)
    """
)


@pytest.fixture
def fake_aws(tmp_path, monkeypatch):
    """Replace the AWS CLI with FAKE_CLI; ``fake_aws(mode)`` sets its behaviour, returns its call log."""
    script = tmp_path / "fake_aws.py"
    script.write_text(FAKE_CLI)
    log = tmp_path / "aws-calls.log"
    monkeypatch.setattr(_sso, "_aws_command", lambda: [sys.executable, str(script)])
    monkeypatch.setenv("DATOM_FAKE_AWS_LOG", str(log))
    expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    monkeypatch.setenv("DATOM_FAKE_AWS_EXPIRY", expiry)

    def use(mode: str):
        monkeypatch.setenv("DATOM_FAKE_AWS_MODE", mode)
        return log

    return use


def calls(log) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("good", ["dev", "my-profile", "team/dev", "a.b@c:d+e%f_g"])
def test_profile_names_aws_allows(good):
    assert datom.sso_credentials(good).profile == good


@pytest.mark.parametrize("bad", ["", "-debug", "--profile", "a b", "a;b", "$(id)", "a\nb", None, 3])
def test_profile_names_aws_does_not_allow(bad):
    with pytest.raises(datom.InvalidArgument):
        datom.sso_credentials(bad)


def test_making_the_function_runs_nothing(fake_aws):
    log = fake_aws("ok")
    datom.sso_credentials("dev")
    assert calls(log) == []


def test_runs_export_credentials_and_returns_the_store_shape(fake_aws):
    log = fake_aws("ok")
    result = datom.sso_credentials("dev")()
    assert calls(log) == ['["configure", "export-credentials", "--profile", "dev", "--format", "process"]']
    assert result["access_key"] == CLI_KEY and result["secret_key"] == CLI_SECRET
    assert result["session_token"] == CLI_TOKEN and result["expiration"]


def test_never_runs_a_shell(fake_aws, monkeypatch):
    fake_aws("ok")
    seen = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        seen.append((args, kwargs))
        return real_run(args, **kwargs)

    monkeypatch.setattr(_sso.subprocess, "run", spy)
    datom.sso_credentials("dev")()
    (args, kwargs), = seen
    assert isinstance(args, list) and kwargs.get("shell") is False


def test_credentials_without_expiration_are_refused(fake_aws):
    fake_aws("no-expiration")
    with pytest.raises(datom.CredentialsInvalid, match="permanent keys") as e:
        datom.sso_credentials("dev")()
    for secret in CLI_SECRETS:
        assert secret not in str(e.value)


def test_failed_command_shows_its_error_output_never_its_standard_output(fake_aws):
    fake_aws("fail")
    with pytest.raises(datom.CredentialsFailed) as e:
        datom.sso_credentials("dev")()
    message = str(e.value)
    assert "Token for dev does not exist" in message and "aws sso login --profile dev" in message
    for secret in CLI_SECRETS:
        assert secret not in message


def test_output_that_is_not_json_is_refused_without_echoing_it(fake_aws):
    fake_aws("not-json")
    with pytest.raises(datom.CredentialsInvalid) as e:
        datom.sso_credentials("dev")()
    assert CLI_KEY not in str(e.value)
    assert e.value.__context__ is None  # the decode error holds the whole output


def test_a_command_that_hangs_is_stopped(fake_aws, monkeypatch):
    fake_aws("slow")
    monkeypatch.setattr(_sso, "TIMEOUT", 1)
    with pytest.raises(datom.CredentialsFailed, match="longer than 1 seconds") as e:
        datom.sso_credentials("dev")()
    assert e.value.__context__ is None  # the timeout error holds partial output
    for secret in CLI_SECRETS:
        assert secret not in str(e.value)


def test_missing_cli_is_explained(monkeypatch):
    monkeypatch.setattr(_sso.shutil, "which", lambda name: None)
    with pytest.raises(datom.CredentialsFailed, match="AWS CLI v2"):
        datom.sso_credentials("dev")()


@pytest.mark.parametrize("shim", [r"C:\tools\aws.cmd", r"C:\tools\AWS.BAT"])
def test_batch_script_cli_is_refused(monkeypatch, shim):
    monkeypatch.setattr(_sso.shutil, "which", lambda name: shim)
    ran = []
    monkeypatch.setattr(_sso.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(datom.CredentialsFailed, match="batch script"):
        datom.sso_credentials("dev")()
    assert ran == []


def test_prints_and_pickles_with_only_the_profile():
    fn = datom.sso_credentials("dev")
    assert repr(fn) == "SsoCredentials(profile='dev')"
    assert "sso_credentials('dev')" in repr(datom.store_s3("bucket", credentials=fn))
    assert pickle.loads(pickle.dumps(fn)).profile == "dev"


def test_store_reads_with_sso_credentials(fake_aws):
    fake_aws("ok")
    with mock_aws():
        setup = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
        )
        setup.create_bucket(Bucket="datom-sso")
        setup.put_object(Bucket="datom-sso", Key="datom/dm/x.json", Body=b"{}")
        store = datom.store_s3("datom-sso", credentials=datom.sso_credentials("dev"))
        signed = []
        store._get_client().meta.events.register(
            "before-send.s3", lambda request, **_: signed.append(request.headers["Authorization"])
        )
        assert store.read_bytes("dm/x.json") == b"{}"
    auth = signed[0].decode() if isinstance(signed[0], bytes) else signed[0]
    assert f"Credential={CLI_KEY}/" in auth


def test_store_passes_the_helpers_error_through_unchanged(fake_aws):
    fake_aws("fail")
    with mock_aws():
        store = datom.store_s3("datom-sso", credentials=datom.sso_credentials("dev"))
        with pytest.raises(datom.CredentialsFailed, match="AWS CLI said"):
            store.read_bytes("dm/x.json")
