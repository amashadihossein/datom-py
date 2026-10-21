"""One ambient AWS setting per test: none of them may change a request (R4, AC4).

Each case plants a single wrong setting -- an environment variable, a file
under ``~/.aws/``, a plugin -- and then reads through ``store_s3()``. Every case
is checked the same way: the request goes to the default us-east-1 host, is
signed with the credential function's key for us-east-1, carries no other
token, and no planted file is opened, no planted command run and no planted
plugin loaded.

The table below maps each case to the part of the client builder in
``_s3._build_client()`` that stops it; removing that part alone turns the case
red (recorded in the commit that added this file). Two cases are stopped by two
parts at once and so stay green when either alone is removed; they are marked.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlsplit

import boto3
import pytest
from botocore.loaders import Loader
from moto import mock_aws

import datom

FUNCTION_KEY = "AKIAFUNCTIONKEY00000"
FUNCTION_SECRET = "function-secret-value-0000"
FUNCTION_TOKEN = "function-session-token-0000"
PLANTED_KEY = "AKIAPLANTEDAMBIENT00"
PLANTED_SECRET = "planted-ambient-secret"
PLANTED_TOKEN = "planted-ambient-token"
PLANTED_HOST = "planted.invalid"
PLANTED_PLUGIN = "datom_planted_botocore_plugin_one"
BUCKET = "datom-ambient"
EXPECTED_HOST = f"{BUCKET}.s3.amazonaws.com"
NOT_INI = "this is not an ini file [[[\n"
NOT_JSON = "{ this is not json"
# A port nothing listens on: a planted metadata endpoint fails at once.
DEAD_URL = "http://127.0.0.1:9"


def function_credentials():
    return {"access_key": FUNCTION_KEY, "secret_key": FUNCTION_SECRET, "session_token": FUNCTION_TOKEN}


# --- what a case can observe --------------------------------------------------------

# Paths opened while the store is in use; None when not recording. An audit hook
# cannot be removed once added, so it is added once and switched on per test.
_OPENED: list[str] | None = None


def _audit(event, args):
    if _OPENED is not None and event == "open" and args and isinstance(args[0], (str, Path)):
        _OPENED.append(str(args[0]))


sys.addaudithook(_audit)


class World:
    """One test's planted setting, and the folders it may use."""

    def __init__(self, monkeypatch, home: Path, tmp: Path) -> None:
        self.env = monkeypatch
        self.home = home
        self.tmp = tmp
        self.marker = tmp / "planted-command-ran"
        self._moto_keys: dict[str, str] = {}

    def aws_file(self, name: str, text: str) -> Path:
        path = self.home / ".aws" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def no_moto_keys(self) -> None:
        # moto's mock_aws sets fake AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY; a case
        # that needs botocore's chain to look further than the environment drops them,
        # and puts them back before mock_aws exits (moto deletes them on exit).
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            if name in os.environ:
                self._moto_keys[name] = os.environ.pop(name)

    def restore_moto_keys(self) -> None:
        os.environ.update(self._moto_keys)

    def corrupt_models(self, root: Path) -> None:
        # botocore raises if it ever parses one of these.
        for rel in ("endpoints.json", "s3/2006-03-01/service-2.json", "s3/2006-03-01/endpoint-rule-set-1.json"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(NOT_JSON)


# --- the cases ----------------------------------------------------------------------


def env_credentials(w: World):
    w.env.setenv("AWS_ACCESS_KEY_ID", PLANTED_KEY)
    w.env.setenv("AWS_SECRET_ACCESS_KEY", PLANTED_SECRET)
    w.env.setenv("AWS_SESSION_TOKEN", PLANTED_TOKEN)


def shared_credentials_file(w: World):
    w.no_moto_keys()
    w.aws_file("credentials", f"[default]\naws_access_key_id = {PLANTED_KEY}\naws_secret_access_key = {PLANTED_SECRET}\n")


def config_credential_process(w: World):
    w.no_moto_keys()
    command = f'"{sys.executable}" -c "open(r\'{w.marker}\', \'w\').close()"'
    w.aws_file("config", f"[default]\ncredential_process = {command}\n")


def env_profile(w: World):
    w.env.setenv("AWS_PROFILE", "planted")
    w.aws_file("config", f"[profile planted]\nregion = eu-west-1\nendpoint_url = https://{PLANTED_HOST}\n")


def env_endpoint_s3(w: World):
    w.env.setenv("AWS_ENDPOINT_URL_S3", f"https://{PLANTED_HOST}")


def env_endpoint(w: World):
    w.env.setenv("AWS_ENDPOINT_URL", f"https://{PLANTED_HOST}")


def config_endpoint(w: World):
    w.aws_file("config", f"[default]\nendpoint_url = https://{PLANTED_HOST}\n")


def config_services_endpoint(w: World):
    w.aws_file(
        "config",
        f"""\
        [default]
        services = planted

        [services planted]
        s3 =
          endpoint_url = https://{PLANTED_HOST}
        """,
    )


def config_s3_addressing(w: World):
    w.aws_file("config", "[default]\ns3 =\n  addressing_style = path\n")


def env_fips(w: World):
    w.env.setenv("AWS_USE_FIPS_ENDPOINT", "true")


def env_dualstack(w: World):
    w.env.setenv("AWS_USE_DUALSTACK_ENDPOINT", "true")


def env_us_east_1_regional(w: World):
    w.env.setenv("AWS_S3_US_EAST_1_REGIONAL_ENDPOINT", "regional")


def env_config_file(w: World):
    path = w.tmp / "planted-config"
    path.write_text(NOT_INI)
    w.env.setenv("AWS_CONFIG_FILE", str(path))


def env_credentials_file(w: World):
    path = w.tmp / "planted-credentials"
    path.write_text(NOT_INI)
    w.env.setenv("AWS_SHARED_CREDENTIALS_FILE", str(path))


def env_region(w: World):
    w.env.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    w.env.setenv("AWS_REGION", "eu-west-1")


def env_container_credentials(w: World):
    w.no_moto_keys()
    w.env.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", f"{DEAD_URL}/creds")


def env_instance_metadata(w: World):
    w.no_moto_keys()
    w.env.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", DEAD_URL)
    w.env.setenv("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "1")
    w.env.setenv("AWS_METADATA_SERVICE_TIMEOUT", "1")


def env_bearer_token(w: World):
    w.env.setenv("AWS_BEARER_TOKEN_S3", "planted-bearer-token")


def customer_models(w: World):
    # botocore computes this from the real home folder at import, so it is moved here.
    root = w.home / ".aws" / "models"
    w.corrupt_models(root)
    w.env.setattr(Loader, "CUSTOMER_DATA_PATH", str(root))


def env_data_path(w: World):
    root = w.tmp / "planted-models"
    w.corrupt_models(root)
    w.env.setenv("AWS_DATA_PATH", str(root))


def env_plugin(w: World):
    plugins = w.tmp / "plugins"
    plugins.mkdir()
    (plugins / f"{PLANTED_PLUGIN}.py").write_text(
        "LOADED = []\n\ndef initialize_client_plugin(client):\n    LOADED.append(client)\n"
    )
    w.env.syspath_prepend(str(plugins))
    w.env.setenv("BOTOCORE_EXPERIMENTAL__PLUGINS", f"planted={PLANTED_PLUGIN}")


#: case -> the part of _build_client() that stops it ("two parts": either alone suffices).
CASES = {
    "env-credentials": (env_credentials, "credential provider"),
    "shared-credentials-file": (shared_credentials_file, "credentials_file emptied"),
    "config-credential-process": (config_credential_process, "config_file emptied"),
    "env-profile": (env_profile, "config_file emptied"),
    "env-endpoint-s3": (env_endpoint_s3, "configured endpoints ignored"),
    "env-endpoint": (env_endpoint, "configured endpoints ignored"),
    "config-endpoint": (config_endpoint, "config_file emptied"),
    "config-services-endpoint": (config_services_endpoint, "config_file emptied"),
    "config-s3-addressing": (config_s3_addressing, "config_file emptied"),
    "env-fips": (env_fips, "constant config store"),
    "env-dualstack": (env_dualstack, "constant config store"),
    "env-us-east-1-regional": (env_us_east_1_regional, "constant config store"),
    "env-config-file": (env_config_file, "constant config store"),
    "env-credentials-file": (env_credentials_file, "constant config store"),
    "env-region": (env_region, "two parts: explicit region, constant config store"),
    "env-container-credentials": (env_container_credentials, "credential provider"),
    "env-instance-metadata": (env_instance_metadata, "credential provider"),
    "env-bearer-token": (env_bearer_token, "token provider"),
    "customer-models": (customer_models, "data loader"),
    "env-data-path": (env_data_path, "two parts: data loader, constant config store"),
    "env-plugin": (env_plugin, "plugins disabled"),
}


@pytest.mark.parametrize("plant", [c[0] for c in CASES.values()], ids=list(CASES))
def test_one_ambient_setting_changes_nothing(plant, monkeypatch, no_ambient_aws, tmp_path):
    global _OPENED
    world = World(monkeypatch, no_ambient_aws, tmp_path)
    sent = []

    def tripwire(request, **_):
        auth = request.headers.get("Authorization") or b""
        token = request.headers.get("X-Amz-Security-Token") or b""
        sent.append(
            (
                urlsplit(request.url).hostname,
                auth.decode() if isinstance(auth, bytes) else auth,
                token.decode() if isinstance(token, bytes) else token,
            )
        )
        if sent[-1][0] != EXPECTED_HOST:  # stop at once rather than retry a wrong host
            raise AssertionError(f"request sent to {sent[-1][0]}")

    with mock_aws():
        boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
        ).create_bucket(Bucket=BUCKET)
        boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
        ).put_object(Bucket=BUCKET, Key="datom/dm/x.json", Body=b"{}")
        plant(world)
        _OPENED = []
        try:
            store = datom.store_s3(BUCKET, credentials=function_credentials)
            client = store._get_client()
            client.meta.events.register_first("before-send.s3", tripwire)
            store.check_reachable()
            assert store.read_bytes("dm/x.json") == b"{}"
            opened = list(_OPENED)
        finally:
            _OPENED = None
            world.restore_moto_keys()

    assert [host for host, _, _ in sent] == [EXPECTED_HOST, EXPECTED_HOST]
    for _, auth, token in sent:
        assert f"Credential={FUNCTION_KEY}/" in auth and "/us-east-1/s3/aws4_request" in auth
        assert token == FUNCTION_TOKEN
    assert client._request_signer._auth_token is None  # botocore internals; no bearer token held
    planted_dirs = (str(world.home / ".aws"), str(tmp_path / "planted"))
    assert [p for p in opened if p.startswith(planted_dirs)] == []
    assert not world.marker.exists(), "a planted credential_process ran"
    plugin = sys.modules.pop(PLANTED_PLUGIN, None)
    assert plugin is None or plugin.LOADED == [], "a planted botocore plugin was loaded"
