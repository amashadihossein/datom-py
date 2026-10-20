"""A credential function for an AWS single sign-on profile (R6).

R datom has no equivalent: R callers pass key values, which they may fetch any
way they like. This helper is the Python side's convenience for the common SSO
case, and it is still an explicit choice by the caller: datom never runs it on
its own.

It runs ``aws configure export-credentials --profile <profile> --format process``
(AWS CLI v2) and returns its result in the shape ``store_s3()`` expects. The AWS
CLI reads ``~/.aws/`` and the SSO token cache by design; that is the caller's
chosen credential source, not datom reading it implicitly (R6.5). The user must
have run ``aws sso login`` first; this helper does not log in.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from ._errors import CredentialsFailed, CredentialsInvalid, InvalidArgument

# The characters AWS's SDKs accept in a profile name (AWS SDK for Java v2,
# ProfileFileReader.VALID_IDENTIFIER). A leading "-" is refused so the AWS CLI
# can never read the profile name as an option.
_PROFILE = re.compile(r"^[A-Za-z0-9_/.%@:+][A-Za-z0-9_\-/.%@:+]*$")

#: How long one ``aws configure export-credentials`` may take, in seconds.
TIMEOUT = 60

# stderr is copied into errors so the user can see why the CLI failed; this
# keeps a runaway message readable.
_MAX_STDERR = 2000


def _aws_command() -> list[str]:
    """The AWS CLI as an argument list, or refuse.

    On Windows a ``.cmd`` or ``.bat`` shim (the pip-installed AWS CLI v1 is one)
    runs through ``cmd.exe`` even from an argument list, which re-parses the
    arguments; AWS CLI v2 installs ``aws.exe``, so only executables are accepted.
    """
    path = shutil.which("aws")
    if path is None:
        raise CredentialsFailed(
            "The AWS CLI (`aws`) was not found. sso_credentials() needs AWS CLI v2: "
            "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
        )
    if path.lower().endswith((".cmd", ".bat")):
        raise CredentialsFailed(
            f"The AWS CLI found at {path!r} is a batch script; sso_credentials() needs the "
            "AWS CLI v2 executable."
        )
    return [path]


def _export_credentials(profile: str) -> dict:
    """Run the AWS CLI once and return its output in ``store_s3()``'s shape.

    Never includes the command's standard output in an error, since that is
    where the keys are (R6.4).
    """
    args = [*_aws_command(), "configure", "export-credentials", "--profile", profile, "--format", "process"]
    timed_out = False
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, shell=False)
    except subprocess.TimeoutExpired:
        timed_out = True  # its .stdout may hold partial output, so it is not chained
    except OSError as e:
        raise CredentialsFailed(f"Could not run the AWS CLI ({type(e).__name__}).") from None
    if timed_out:
        raise CredentialsFailed(f"`aws configure export-credentials` took longer than {TIMEOUT} seconds.")
    if done.returncode != 0:
        stderr = (done.stderr or "").strip()[:_MAX_STDERR] or "(no error output)"
        raise CredentialsFailed(
            f"`aws configure export-credentials --profile {profile}` failed with exit code "
            f"{done.returncode}. If the session expired, run `aws sso login --profile {profile}`. "
            f"AWS CLI said: {stderr}"
        )
    return _parse_process_output(done.stdout)


def _parse_process_output(stdout: str) -> dict:
    """The ``--format process`` JSON, checked; messages never contain a value."""
    parsed: Any = None
    try:
        parsed = json.loads(stdout)
    except ValueError:
        pass  # raised below, outside the except: the decode error holds the whole output
    if not isinstance(parsed, dict):
        raise CredentialsInvalid("The AWS CLI's output was not a JSON object.")
    for field in ("AccessKeyId", "SecretAccessKey"):
        if not isinstance(parsed.get(field), str) or parsed[field] == "":
            raise CredentialsInvalid(f"The AWS CLI's output has no `{field}`.")
    if parsed.get("Expiration") in (None, ""):
        raise CredentialsInvalid(
            "The AWS CLI returned credentials with no expiration. That is what permanent keys "
            "stored as plain text produce, not single sign-on; sso_credentials() accepts only "
            "temporary credentials. Check the profile, or pass a credential function of your own."
        )
    out = {
        "access_key": parsed["AccessKeyId"],
        "secret_key": parsed["SecretAccessKey"],
        "expiration": parsed["Expiration"],
    }
    if parsed.get("SessionToken"):
        out["session_token"] = parsed["SessionToken"]
    return out


class SsoCredentials:
    """A credential function for one AWS SSO profile. Make one with :func:`datom.sso_credentials`.

    Holds only the profile name, so it prints and pickles without a secret.
    """

    def __init__(self, profile: str) -> None:
        self.profile = profile
        # What the store's repr() shows for this function.
        self.__qualname__ = f"sso_credentials({profile!r})"

    def __repr__(self) -> str:
        return f"SsoCredentials(profile={self.profile!r})"

    def __call__(self) -> dict:
        return _export_credentials(self.profile)


def sso_credentials(profile: str) -> SsoCredentials:
    """A credential function for an AWS single sign-on profile.

    Pass the result to :func:`datom.store_s3`::

        store = datom.store_s3("my-bucket", credentials=datom.sso_credentials("my-profile"))

    Each call runs ``aws configure export-credentials --profile <profile>
    --format process`` (AWS CLI v2), never through a shell. Run
    ``aws sso login --profile <profile>`` first; this does not log in.
    Credentials without an expiration are refused, because that is what
    permanent plain-text keys produce. Nothing runs until the store first needs
    credentials.
    """
    if not isinstance(profile, str) or not _PROFILE.match(profile):
        raise InvalidArgument(
            "`profile` must be one AWS profile name: letters, digits and - / . % @ _ : +, "
            "not starting with -."
        )
    return SsoCredentials(profile)
