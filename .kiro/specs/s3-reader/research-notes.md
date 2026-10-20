# s3-reader research notes (datom-py, spec `s3-reader`)

Raw findings for the design of the Python S3 reader. Not a design. Each claim is tagged:
**[V: source]** verified by reading that source or running that probe, **[U]** unverified.

## 0. Environment used for the checks

- Repo `.venv` is Python 3.9.6 with only pip/setuptools; it is unusable (project needs >=3.10). Nothing was installed into it. [V: `.venv/pyvenv.cfg`, `ls .venv/bin`]
- Global pyenv Python is 3.14.2 with `datom` installed editable, but no pandas/pyarrow/boto3/moto there. [V: `python3 -m pip list`]
- For inspection only, two throwaway venvs in `/tmp` (outside the repo, `pyproject.toml` untouched):
  - `/tmp/datom-s3-inspect` (Python 3.14.2): boto3 1.43.114, botocore 1.43.114, moto 5.2.4.
  - `/tmp/datom-s3-inspect-140` (Python 3.14.2): boto3 1.40.0, botocore 1.40.0, moto 5.2.4.
- Downloaded wheels (not installed) for static reading: botocore 1.34.0, 1.36.0, 1.37.0, 1.38.0, 1.39.0, 1.40.0, 1.41.0 (1.42.0 download failed), awscli 1.46.1. Under `/tmp/datom-s3-inspect/wheels/`.
- Fetched for reading: AWS CLI v2 `awscli/customizations/configure/exportcreds.py` (GitHub `aws/aws-cli` branch `v2`), AWS SDK for Java v2 `ProfileFileReader.java`, AWS docs page "Shared config and credentials files" (file-format.html).
- Probe scripts (throwaway, not project code): `/tmp/datom-s3-inspect/proto.py`, `proto_build.py` (minimal isolated client builder), `proto_build_full.py` (fuller variant), `proto2.py` … `proto7.py`.
- No probe touched real AWS: everything ran under `moto.mock_aws()`; planted endpoints that escaped moto only reached `evil.example` / `127.0.0.1:9` and failed. One fake 301 redirect was produced by a `before-send` handler returning a canned `AWSResponse`.

## 1. Existing datom-py structure and where S3 plugs in

- `src/datom/_store.py`: `build_storage_key(prefix, key)` -> `{prefix}/datom/{key}` (strips slashes, drops empty parts), port of R `.datom_build_storage_key()`. `LocalStore(path, prefix)` with `backend = "local"`, `check_reachable()` (raises `StoreUnreachable` if folder missing; R would create it), `exists(key) -> bool`, `read_bytes(key) -> bytes` (raises `NotFound`), private `_path()` with a second-line namespace-escape check raising `InvalidAddress`. `store_local()` factory. Module docstring already says "An S3 store will implement the same methods." [V: read file]
- `src/datom/_conn.py`: `Conn` is `@dataclass(frozen=True)` with `store`, `project_name`; `__repr__` shows project and `repr(store)`. `get_conn(store, project_name)`: validates name; duck-types store (`read_bytes`, `exists` callable); runs `_governance_marker(store)` FIRST (any exception -> treated as no marker, swallowed), warns `GovernanceAttachedWarning` if valid marker; THEN `store.check_reachable()`. Same order as R `.datom_get_conn_reader()` (gov probe, then `.datom_check_data_reachable()`). [V: read both]
  - Consequence for S3: first S3 request at connect is `HeadObject` on `.metadata/governance.json` (its failure is swallowed), then `HeadBucket`. If the credential function fails, it is called once in the probe (error swallowed) and again by `check_reachable()` (error propagates). [V: code reading; double call not run]
- `src/datom/_metadata.py`: `read_json(store, key)` -> `store.read_bytes` + `json.loads`, `DocumentUnreadable` on parse error. `read_metadata`, `resolve_version` are backend-neutral. [V]
- `src/datom/_table.py` / `_set.py`: build keys `{name}/{data_sha}.parquet|.json` after `validate_name` / `validate_sha`; hash before parse. Backend-neutral (only `conn.store.read_bytes`). [V]
- `src/datom/_query.py`: `history()` re-raises `NotFound` with its own message (chained). `read_manifest()` wraps ANY `DatomError` from the read into `ManifestUnreadable` ("Underlying error: {e}") — so an access denial or unreachable store on the manifest surfaces from `list_artifacts()` as `manifest_unreadable` with the S3 error as cause. Matches R `.datom_read_manifest()` (`tryCatch` around the read). [V: both read]
- `src/datom/_set.py` `_fetch()` wraps any `DatomError` into `MemberProjectMismatch` when member project != conn project. [V]
- `src/datom/_errors.py`: `DatomError` base (`code="datom_error"`), existing codes include `invalid_argument`, `invalid_address`, `store_unreachable`, `not_found`, ... No access-denied or credential errors yet. `InvalidArgument` also subclasses `ValueError`. [V]
- `src/datom/__init__.py`: re-exports everything, `__all__` explicit; `LocalStore`, `store_local` from `_store`. A new `store_s3` / `sso_credentials` export must not import botocore at import time (R7.1). [V]
- `pyproject.toml`: hatchling; deps `pyarrow>=20`, `pandas>=2.0`; extras only `dev = ["pytest>=7"]`; `requires-python >=3.10`. [V]
- CI `.github/workflows/tests.yaml`: `pip install -e ".[dev]"`, pytest on Ubuntu 3.10–3.13, Windows 3.13, macOS 3.13. [V]
- Tests:
  - `tests/reader_cases.py` downloads R datom tarball at pinned `DATOM_COMMIT = 557a3026…`, unpacks each case's `storage.zip` to `storage/`; `CASES_DIR`. Each case's storage has a top-level `datom/` folder, so S3 keys are `{prefix}/` + relative path. [V]
  - `tests/test_reader_cases.py`: `conn_for(case_dir)` builds a local store; `run_call` dispatches ops; `table_result` re-reads the parquet from local disk to prove which file was read (works unchanged if conn is S3); `SKIPPED = {}`; `test_every_case_is_run` (>= 28); `test_reading_every_case_writes_nothing` (local mtimes snapshot). 29 entries at the pin (28 case folders + README). [V: `git ls-tree` in R repo, 29 names incl. README]
  - `tests/conftest.py`: `case_store` fixture (editable local copy). [V]
  - `tests/test_rules.py`: includes `test_store_has_no_way_to_write` (no write/put/delete attrs) and `test_conn_holds_only_store_and_project` (`vars(conn.store) == {"root","prefix"}`) — patterns to mirror for S3. [V]
  - `tools/check_tests_catch_breaks.py`: list of `(label, file, exact text, replacement)`; copies `src/`, applies one edit, runs `pytest -q -x`, expects failure; 20 breaks today. Text must occur exactly once in the file. New S3 breaks can be added the same way (file = new module name). [V]
- README "Not yet" lists S3; FINDINGS.md has nothing S3-specific. [V]

## 2. R reference (read-only)

- `datom_store_s3()` in `R/store.R`: args `bucket, prefix=NULL, region="us-east-1", access_key, secret_key, session_token=NULL, validate=TRUE`. Validation: bucket/region/access/secret single non-empty strings; prefix single string or NULL; session_token NULL or non-empty string. No bucket-name pattern check, no endpoint argument. If `validate`, calls `.datom_validate_s3_store()`. Stores the raw keys in the object; `print.datom_store_s3()` masks them via `.datom_mask_secret()` (first 4 chars + `****` for the access key; secret/token fully masked). [V]
- `.datom_validate_s3_store()` in `R/store.R`: builds a paws client, `head_bucket(Bucket=)`. Error classification by regex on the message: `403|Forbidden|AccessDenied` -> "credentials are invalid or lack access"; `404|NoSuchBucket|NotFound` -> "bucket does not exist"; else "Failed to verify access" with underlying message. [V]
- `.datom_check_data_reachable()` in `R/ref.R`: S3 -> `head_bucket`; 403 -> abort (migrated / not migrated wording); 404 -> abort "does not exist"; **any other error (network) -> WARN and proceed ("offline use")**. Local -> abort if dir missing. [V] Python requirement R2.3 says unreachable endpoint raises `StoreUnreachable` — a divergence from R's warn-and-continue at connect; needs an explicit decision. [V: both texts]
- `.datom_get_conn_reader()` in `R/conn.R`: gov probe via `.datom_storage_read_governance_json()` wrapped in `tryCatch(..., error = NULL)`, warns, then `.datom_check_data_reachable()`. [V]
- `.datom_s3_client()` in `R/utils-s3.R`: `paws.storage::s3(config = list(credentials = list(creds = ...), region = region, endpoint = endpoint?))`. Endpoint only from an internal arg (no user-facing endpoint on the store). [V] Whether paws ignores env vars / `~/.aws` when explicit creds are given, and whether paws follows S3 region redirects (301 / `x-amz-bucket-region`): [U] (paws source not read).
- `.datom_s3_exists()`: `head_object`; returns FALSE only if message matches `404|NoSuchKey|Not Found`; any other error rethrown with Root/Key and underlying message. [V]
- `.datom_s3_read_json()` / `.datom_s3_download()`: `get_object`; errors re-aborted with Root, Key, "Underlying error: {conditionMessage(e)}" and `parent = e`. R includes the SDK's message text in its errors. [V]
- R also has `.datom_s3_upload`, `_write_json`, `_delete_prefix`, `_list_objects` (writer/maintenance; out of scope for a reader). [V]
- Read contract: R repo `tests/testthat/fixtures/reader-cases/README.md`: case = `case.json` + `storage.zip` (unpacks to `datom/`); ops `read_table`, `read_set`, `fetch_member`, `history`, `list`; project always `STUDY_001`; error-name table. No S3-specific content; cases are backend-agnostic. [V]

## 3. botocore: making a client that nothing ambient can influence

All of the following verified on botocore 1.43.114 and 1.40.0 by running `proto.py` / `proto5.py` under moto unless stated otherwise.

### 3.1 Where ambient settings enter botocore (source reading)

- `Session.__init__` registers components; `_register_config_store()` builds `ConfigValueStore(mapping=create_botocore_default_config_mapping(self))`, i.e. a chain per variable: instance var -> env var -> config file -> default. [V: `botocore/session.py`, `botocore/configprovider.py`]
- `BOTOCORE_DEFAUT_SESSION_VARIABLES` (sic) in `configprovider.py` lists the env vars: `AWS_DEFAULT_PROFILE`/`AWS_PROFILE`, `AWS_DEFAULT_REGION` (note: `AWS_REGION` is not in this table), `AWS_DATA_PATH`, `AWS_CONFIG_FILE` (default `~/.aws/config`), `AWS_CA_BUNDLE`, `AWS_SHARED_CREDENTIALS_FILE` (default `~/.aws/credentials`), IMDS vars, `AWS_USE_DUALSTACK_ENDPOINT`, `AWS_USE_FIPS_ENDPOINT`, `AWS_IGNORE_CONFIGURED_ENDPOINT_URLS`, `AWS_DEFAULTS_MODE`, `AWS_RETRY_MODE`, `AWS_MAX_ATTEMPTS`, checksum vars, `AWS_S3_DISABLE_EXPRESS_SESSION_AUTH`, `BOTOCORE_TCP_KEEPALIVE`, etc. Plus `DEFAULT_S3_CONFIG_VARS` (`s3` section: addressing_style, use_accelerate_endpoint, use_dualstack_endpoint, use_arn_region / `AWS_S3_USE_ARN_REGION`, us_east_1_regional_endpoint / `AWS_S3_US_EAST_1_REGIONAL_ENDPOINT`, `AWS_S3_DISABLE_MULTIREGION_ACCESS_POINTS`) and `DEFAULT_PROXIES_CONFIG_VARS`. [V: `configprovider.py`]
- `Session.full_config` reads `config_file` via `configloader.load_config()` and merges profile names from `credentials_file` via `raw_config_parse()`. `raw_config_parse(None)` returns `{}` without opening anything (path is None). [V: `session.py full_config`, `configloader.raw_config_parse`]
- `Session.create_client()` (decorated `@with_current_context()`): resolves region (`_resolve_region_name`: explicit arg > `config.region_name` > config var `region`), `verify = get_config_variable('ca_bundle')` if not passed, credentials: explicit key args > `self.get_credentials()` (component `credential_provider`, default `botocore.credentials.create_credential_resolver` = full default chain incl. env, shared files, `credential_process`, SSO, web identity, container, IMDS); `auth_token = self.get_auth_token()` (component `token_provider`); `_resolve_defaults_mode` (mode `auto` triggers IMDS region lookup in `resolve_auto_mode`); `_add_configured_endpoint_provider()` installs `ConfiguredEndpointProvider(full_config, scoped_config)` for `endpoint_url` (this is what honours `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_S3`, `endpoint_url` and `services` sections); passes `scoped_config=self.get_scoped_config()` to `ClientCreator`; at the end `_register_client_plugins(client)`. [V: `session.py create_client`]
- `ClientArgsCreator._compute_configured_endpoint_url()`: explicit `endpoint_url` wins; else if `_ignore_configured_endpoint_urls(client_config)` (Config attr, else config var) -> no configured endpoint; else `config_store.get_config_variable('endpoint_url')`. [V: `botocore/args.py`]
- `ConfigValueStore.get_config_variable(name)` returns `None` for a name not in its mapping (so `s3` and `proxies_config` sections are simply None if not supplied). [V: `configprovider.py`]
- `Session._register_data_loader` -> `create_loader(get_config_variable('data_path'))`; `Loader.__init__` with `include_default_search_paths=True` appends `CUSTOMER_DATA_PATH` (`~/.aws/models`, computed at import time via `os.path.expanduser('~')`) and `BUILTIN_DATA_PATH`. [V: `botocore/loaders.py`]
- `botocore.tokens.create_token_resolver(session)` = `[ScopedEnvTokenProvider, SSOTokenProvider]`; `ScopedEnvTokenProvider` reads `AWS_BEARER_TOKEN_<SIGNING_NAME>` (e.g. `AWS_BEARER_TOKEN_S3`) when asked with a signing name. [V: `tokens.py`, `utils.get_token_from_environment`]
- Plugins: `botocore/plugin.py` (module docstring: "considered private and subject to abrupt breaking changes"). `get_botocore_plugins()` returns `PluginContext.plugins` if a context is set, else `os.environ['BOTOCORE_EXPERIMENTAL__PLUGINS']`; `Session._register_client_plugins()` returns early if the value is `"DISABLED"` or empty, else imports each `name=module` and calls `module.initialize_client_plugin(client)`. [V: `plugin.py`, `session.py`]
  - Present since botocore 1.40.0; absent in 1.39.0 and earlier (no `plugin.py`, no `_register_client_plugins`). [V: wheel listing 1.34.0–1.41.0]
- Remaining direct `os.environ` reads in botocore 1.43.114 that the knobs below do NOT stop [V: grep of `botocore/*.py`]:
  - `REQUESTS_CA_BUNDLE` in `endpoint.py` (used when `verify` is None) — CA bundle, allowed by R4.3.
  - Proxy env vars via `utils.get_environ_proxies()` — allowed by R4.3.
  - `SSLKEYLOGFILE` in `httpsession.py` (TLS key logging to a file; Python's `ssl` honours it too) — not identity/destination, but a traffic-decryption vector. Not blockable reasonably. [V source; risk judgement mine]
  - `AWS_NEW_RETRIES_2026` read at import in `configprovider.py` (`_DEFAULT_RETRY_MODE = 'standard' if NEW_RETRIES_ENABLED else 'legacy'`) — retry behaviour only.
  - `AWS_EXECUTION_ENV` (user agent; and `resolve_auto_mode`, unreachable with defaults_mode legacy), `AWS_LAMBDA_FUNCTION_NAME`/`_X_AMZN_TRACE_ID` (trace header), `BOTO_DISABLE_CRT`, `BOTO_EXPERIMENTAL__*` toggles, `AWS_EC2_METADATA_DISABLED` (IMDS fetcher, unused without IMDS providers), `AWS_LOGIN_CACHE_DIRECTORY` (login provider, unused).

### 3.2 The knob set that worked (minimal builder, `proto_build.py`)

```python
s = botocore.session.Session()
values = {name: opts[2] for name, opts in s.SESSION_VARIABLES.items()}   # every var at its default
values.update(config_file=None, credentials_file=None, ignore_configured_endpoint_urls=True)
s.register_component("config_store", ConfigValueStore(mapping={k: ConstantProvider(v) for k, v in values.items()}))
s.register_component("data_loader", Loader(extra_search_paths=[Loader.BUILTIN_DATA_PATH], include_default_search_paths=False))
s.register_component("credential_provider", CredentialResolver(providers=[_Provider(refresh)]))   # one custom provider
s.register_component("token_provider", TokenProviderChain(providers=[]))
tok = set_plugin_context(PluginContext(plugins="DISABLED"))
try:
    client = s.create_client("s3", region_name=region)
finally:
    reset_plugin_context(tok)
```
- `_Provider` is a `CredentialProvider` subclass (`METHOD = CANONICAL_NAME = "custom-datom"`, botocore asks non-botocore providers to prefix with `custom`) whose `load()` returns `DeferredRefreshableCredentials(refresh, METHOD, time_fetcher=...)`. [V: `credentials.py CredentialProvider` comment; probes]
- `Session.register_component()` is public API; `ConfigValueStore`, `ConstantProvider`, `Loader`, `CredentialResolver`, `CredentialProvider`, `DeferredRefreshableCredentials`, `TokenProviderChain` are importable classes but botocore does not document this exact use; `botocore.plugin` is explicitly private. [V: source; "public vs private" is my reading]
- Result with every ambient source planted (env: `AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN`, `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_DEFAULT_REGION`, `AWS_REGION`, `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_S3`, `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_DATA_PATH`, `AWS_USE_FIPS_ENDPOINT`, `AWS_USE_DUALSTACK_ENDPOINT`, `AWS_DEFAULTS_MODE=auto`, `AWS_S3_US_EAST_1_REGIONAL_ENDPOINT=regional`, `AWS_EC2_METADATA_SERVICE_ENDPOINT`, `AWS_CONTAINER_CREDENTIALS_FULL_URI`, `BOTOCORE_EXPERIMENTAL__PLUGINS`, `AWS_BEARER_TOKEN_S3`, `AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=false`, `HOME`; files: `~/.aws/config` with region, `credential_process=/bin/false`, `endpoint_url`, `s3` subsection, a `services` section; `~/.aws/credentials` with wrong keys; a plugin module on `sys.path`):
  - credential function NOT called when the client is built (0 calls); called once at first request. [V both versions]
  - every request went to `https://{bucket}.s3.amazonaws.com/...` (us-east-1 default), `Authorization: AWS4-HMAC-SHA256 Credential=<function key>/<date>/us-east-1/s3/aws4_request`, `X-Amz-Security-Token` = function's token. [V both]
  - no file under the planted temp dir or `~/.aws` was opened (Python audit hook on `open`). [V both]
  - plugin not loaded. [V]
- Remove-one-knob probe (`proto5.py`), effect of dropping each knob alone [V both versions]:

| Knob dropped | What got through |
|---|---|
| constant config store | endpoint -> `AWS_ENDPOINT_URL_S3` (request sent to evil host, 5 attempts, `EndpointConnectionError`); config + credentials files opened |
| `config_file=None` | `~/.aws/config` opened; request itself unchanged |
| `credentials_file=None` | `~/.aws/credentials` opened; request unchanged |
| `ignore_configured_endpoint_urls=True` | endpoint -> planted `AWS_ENDPOINT_URL_S3` |
| builtin-only `Loader` | `~/.aws/models/endpoints.json` opened (`CUSTOMER_DATA_PATH` had to be monkeypatched; it is computed at import) |
| custom `credential_provider` | request signed with env `AWS_ACCESS_KEY_ID` |
| empty `token_provider` | `session.get_auth_token(signing_name="s3")` returns the env bearer token; S3 request still sigv4 with function key (no observable request change today) |
| plugin context `DISABLED` | planted plugin module imported and `initialize_client_plugin(client)` called |

  - Testing note: "file opened but request unchanged" rows are only observable by detecting the read (audit hook, or a corrupt planted file that would crash botocore if parsed); the token row only via the session, not via a request. [V by the probe outcomes; test strategy is a suggestion]
- Fuller variant (`proto_build_full.py`: explicit `Config(region_name, signature_version='s3v4', ignore_configured_endpoint_urls=True, use_fips_endpoint=False, use_dualstack_endpoint=False, defaults_mode='legacy', s3={...})` plus constants for `s3` section) also works; adding the `s3` config section plus a planted endpoint caused `EndpointResolutionError: A custom endpoint cannot be combined with S3 Accelerate` in an early probe where the plant leaked (seeding client), showing `s3` subsection settings are live in a default session. [V]
- `s3_disable_express_session_auth` config var does not exist in 1.40.0 (exists in 1.43.114). [V: wheel grep]
- Default endpoints with the isolated client [V `proto6.py`, 1.43]: eu-west-1 -> `https://{bucket}.s3.eu-west-1.amazonaws.com/`; dotted bucket in us-east-1 -> path style `https://s3.amazonaws.com/{bucket}`; dotted in eu-west-1 -> `https://s3.eu-west-1.amazonaws.com/{bucket}`.
- Bad region string -> `create_client` raises `InvalidRegionError` (a `ValueError`), message echoes the region. `botocore.utils.validate_region_name()` exists in 1.40.0 and 1.43.114. [V]
- Botocore client objects are not picklable (`PicklingError: Can't pickle <class 'botocore.client.S3'>`). [V both]
- Minimum version: everything above works on botocore 1.40.0 (oldest with `botocore.plugin`/`PluginContext`); `ScopedEnvTokenProvider` (bearer env) appeared in 1.39.0. botocore 1.40.0 `Requires-Python >=3.9`; 1.43.114 `>=3.10`; both require `python-dateutil>=2.1,<3`, `jmespath`, `urllib3`. moto 5.2.4 `>=3.10`. [V: wheel/dist-info METADATA] Not checked: anything between 1.41 and 1.43.114 except by assumption; Python 3.10–3.13 (all probes ran on 3.14.2). [U]

### 3.3 S3 region redirect (a server response can change region/endpoint)

- `ClientCreator._register_s3_events()` always registers `S3RegionRedirectorv2` on S3 clients: on `needs-retry.s3` it handles 301/302/307, `PermanentRedirect`, `AuthorizationHeaderMalformed` with Region, HeadObject/HeadBucket 301/400; gets the region (from `x-amz-bucket-region`, error body, or a HeadBucket), re-signs for the new region and re-sends; caches bucket->region and pre-routes later calls (`redirect_from_cache` on `before-endpoint-resolution.s3`). [V: `utils.py S3RegionRedirectorv2`, `client.py`]
- With a fake 301 + `x-amz-bucket-region: eu-west-1`, the isolated us-east-1 client followed the redirect and succeeded, re-sending to `https://datom-eu.s3.eu-west-1.amazonaws.com/` signed for eu-west-1. [V `proto7.py`, both versions] => without an extra guard, R4.2 ("the given region") does not hold on real AWS for a bucket in another region.
- moto does NOT emulate the redirect: a us-east-1 client reading a bucket created in eu-west-1 just succeeds against the global endpoint. Tests of the redirect need a canned response. [V `proto2.py`]
- A `before-send.s3` handler that parses the signing region from `Authorization` (`^AWS4-HMAC-SHA256 Credential=[^/]+/\d{8}/([^/]+)/s3/aws4_request,`) and raises when it differs from the store's region refused the redirected attempt; the exception propagated to the caller once (not retried). [V `proto7.py`, both] Same handler refused `ListObjectsV2` by operation name (`event_name` suffix). [V]
- `before-send` handlers: botocore's `Endpoint._do_get_response` emits `before-send.{service}.{op}`, catches any `Exception` from it as `(None, e)` (logging "Exception received when sending HTTP request." at DEBUG with `exc_info`), then the retry handler re-raises non-retryable exceptions. [V: behaviour in probes; endpoint code partially read] [U: exact retry classification code not read line by line]
- moto 5 intercepts through a handler appended to botocore's built-in `before-send` handlers, so an isolated `Session()` (with `include_builtin_handlers=True`, the default) is still mocked. [V: probes work; mechanism U — moto source not read]
- S3 Express (directory buckets): `S3ExpressIdentityResolver(client, RefreshableCredentials)` registered on every S3 client; its cache calls `self._client.create_session(Bucket=...)` on the SAME client, so a `before-send` op allow-list would see and refuse `CreateSession`. [V: `utils.py S3ExpressIdentityCache`, `client.py _register_s3express_events`] Under moto a `--x-s3` bucket name produced only a `GetObject` (no CreateSession) — moto/rules did not route to express. [V `proto4.py`; why: U]
- Access-point / MRAP ARNs as `Bucket` change host (and MRAP uses sigv4a with region `*`, which the region regex would refuse). [U — not probed]

## 4. Credentials: RefreshableCredentials behaviour

- `RefreshableCredentials` timing: `_DEFAULT_ADVISORY_REFRESH_TIMEOUT = 15*60`, `_DEFAULT_MANDATORY_REFRESH_TIMEOUT = 10*60`. `_refresh()`: if seconds remaining < advisory -> try refresh (non-blocking lock); advisory failure is logged (WARNING, `exc_info=True`) and swallowed (old creds kept); within mandatory window, failure is logged and re-raised. `_expiry_time is None` -> never refresh. [V: `credentials.py`]
- `DeferredRefreshableCredentials(refresh_using, method, time_fetcher)`: no initial creds; `refresh_needed()` is True while `_frozen_credentials is None`, so the first access is a mandatory refresh. [V]
- `_set_from_data(data)` requires keys `access_key`, `secret_key`, `token`, `expiry_time` (else `CredentialRetrievalError`), and does `self._expiry_time = parse(data['expiry_time'])` with dateutil `parse` — so `expiry_time=None` is NOT accepted on refresh; "no expiry" needs a far-future value (probes used `"9999-01-01T00:00:00+00:00"`). [V source + probe]
- After a refresh, if still expired: logs WARNING and raises plain `RuntimeError("Credentials were refreshed, but the refreshed credentials are still expired.")` — not a botocore exception class. [V: source + probe `proto2.py` "expired"] => a datom wrapper should reject an already-expired result itself, before botocore does.
- An exception from `refresh_using` propagates out of the client call unchanged (probe raised `RuntimeError('keyring locked')`, `__cause__` None); request signing (`create_request`) runs outside the send try/except, so no retry. [V `proto2.py`; signing location partly U]
- `time_fetcher` default `_local_now()` = `datetime.now(tzlocal())`; passing an aware-UTC clock works; tests can move time if the clock is looked up at call time (probe used `lambda: _now()` and replaced module `_now`). [V]
- Measured call counts [V both versions]:
  - expiry in 2 h: 3 requests -> 1 call; no expiry (far-future): 1 call; expiry in 12 min (inside advisory): 3 requests -> 3 calls (function called on every request while < 15 min left);
  - clock moved to 10 min before a 2 h expiry: second request -> 2nd call. => AC6 testable without sleeping.
- What the probes did NOT cover: concurrent refresh from threads; advisory-window failure path (swallowed, old creds reused). [U]
- Logging of secrets with DEBUG on [V `proto.py`/`proto2.py`, both versions]: secret key never logged; session token and access key id logged by `botocore.endpoint` ("Sending http request: <AWSPreparedRequest ... headers={... 'Authorization': b'AWS4-HMAC-SHA256 Credential=AKIA...', 'X-Amz-Security-Token': ...}") and `botocore.auth` (canonical request contains `x-amz-security-token:<token>`). A `logging.Filter` on `botocore.endpoint`, `botocore.auth`, `botocore.parsers` dropping records whose message contains `x-amz-security-token`, `authorization`, `credential=` or `awsaccesskeyid` (case-insensitive) removed all of them (~193 other records kept). Logger-level filters run in `Logger.handle` before propagation, so root handlers never see dropped records. [V `proto3.py`] Such a filter is process-wide (affects every botocore client in the process). [V by construction]
  - `botocore.parsers` logs "Response body" at DEBUG; real S3 `SignatureDoesNotMatch` bodies include `AWSAccessKeyId`, `StringToSign`, `CanonicalRequest` (which lists the security-token header value). [U for real S3 body content — from memory of S3 behaviour; moto's body did not include them]
- `botocore.credentials` WARNING with `exc_info` on any refresh failure goes to stderr via logging's last-resort handler when the app configures no logging: the user sees the traceback (our message + chained user exception) once from botocore, then again when raised. [V source; stderr behaviour from Python logging docs, U not run]

## 5. Errors: what botocore/S3 return and what the text can contain

- `ClientError` message template: `"An error occurred ({Code}) when calling the {Op} operation{retry_info}: {Message}"`; `.response` holds the full parsed error dict + `ResponseMetadata` (request id, host id, headers); `__reduce__` pickles `error_response`. [V: `exceptions.py ClientError`]
- S3 error body parsing (`RestXMLParser._parse_error_from_body`): `{'Error': <all XML children of <Error> except RequestId/HostId>, 'ResponseMetadata': ...}` — every field the server sends ends up in `e.response['Error']`. Body-less errors (HEAD requests) -> `{'Code': str(status), 'Message': http reason}`. [V: `parsers.py`]
- Observed under moto [V `proto.py`, `proto2.py`, both versions]:

| Call | Result |
|---|---|
| HeadObject missing key | `Code '404'`, `Message 'Not Found'`, status 404 (body-less) |
| GetObject missing key | `NoSuchKey`, status 404, `Error` has `Key` |
| HeadBucket missing bucket | `Code '404'` |
| GetObject missing bucket | `NoSuchBucket`, `Error` has `BucketName` |
| HeadBucket, IAM user allowed only `s3:GetObject` | `AccessDenied` (moto sends a body; real S3 HEAD 403 is body-less -> `Code '403'` [U]) |
| HeadObject missing key, same user | 404 (real S3 returns 403 for a missing key when the caller lacks `s3:ListBucket` [U, AWS behaviour from memory]) |
| unknown access key | `InvalidAccessKeyId` |
| wrong secret | `SignatureDoesNotMatch`; moto's `Error` dict had only Code/Message, no secret |

- moto IAM enforcement works with `from moto.core import set_initial_no_auth_action_count` decorating a `@mock_aws` function; the count must cover setup calls (5 needed: create bucket, put object, create user, put policy, create access key). [V `proto2.py`]
- Statuses for credential-ish codes in real S3 (AccessDenied/InvalidAccessKeyId/SignatureDoesNotMatch 403; ExpiredToken/InvalidToken 400; AllAccessDisabled/AccountProblem 403): [U — from memory of the S3 error-code table, page not fetched]
- `BotoCoreError` subclasses and message templates [V: `botocore.exceptions` introspection]: `EndpointConnectionError` "Could not connect to the endpoint URL: {endpoint_url}"; `ConnectTimeoutError`, `ReadTimeoutError`, `ConnectionClosedError` (endpoint URL); `SSLError` "SSL validation failed for {endpoint_url} {error}"; `HTTPClientError` / `ConnectionError` "{error}" (underlying urllib3 text); `ProxyConnectionError` uses `mask_proxy_url()` (proxy credentials masked); `ResponseStreamingError` "{error}"; `IncompleteReadError`; `FlexibleChecksumError` (response checksum; GetObject requests carry `x-amz-checksum-mode: ENABLED` by default); `ParamValidationError` "{report}"; `NoCredentialsError`; `CredentialRetrievalError` "{provider}: {error_msg}"; `InvalidRegionError`; `EndpointResolutionError`.
  - Risk: `{error}` texts come from urllib3/ssl and can include URLs (and in principle proxy userinfo); server `Message` fields are server-controlled text. Building datom messages only from our own strings + the error `Code` + HTTP status + exception class name avoids echoing any of it. [V templates; leak potential is a judgement]
- Python exception chaining: `raise X from None` still sets `X.__context__` (only suppresses display); to keep a botocore exception (whose `.response` may hold a canonical request with the token) off a datom error entirely, raise after leaving the `except` block. Exceptions do not pickle `__cause__`/`__context__`. [Python semantics, U not run here]

## 6. SSO helper facts

- `aws` CLI is not installed on this machine. [V `which aws`]
- `aws configure export-credentials` exists only in AWS CLI v2: the awscli 1.46.1 wheel has no `exportcreds.py`. [V wheel listing]
- AWS CLI v2 `ConfigureExportCredentialsCommand` [V: fetched `exportcreds.py`]: default `--format process`; `CredentialProcessFormatter` writes to stdout `json.dumps({'Version': 1, 'AccessKeyId', 'SecretAccessKey', ['SessionToken'], ['Expiration']}, indent=2)` + newline; `Expiration` = botocore credentials' private `_expiry_time.isoformat()` when present, omitted otherwise (so static keys from `~/.aws/credentials` produce no `Expiration`); failures raise `ConfigurationError("Unable to retrieve credentials: ...")` (CLI prints to stderr, non-zero exit [U exit code value]); uses recursion env var `_AWS_CLI_PROFILE_CHAIN`.
  - `isoformat()` of an aware datetime -> e.g. `2026-10-19T18:00:00+00:00`; Python 3.10's `datetime.fromisoformat` parses that form but not a trailing `Z` or basic format (3.11+ parses both). [Python docs, U not run on 3.10]. `dateutil.parser.isoparse` (python-dateutil >= 2.7) is a stricter consistent alternative; botocore already depends on python-dateutil. [V botocore METADATA; isoparse version U]
- Profile name characters: AWS SDK for Java v2 `ProfileFileReader.VALID_IDENTIFIER = ^[A-Za-z0-9_\-/.%@:\+]*$`, warning text "alphanumeric with only these special characters: - / . % @ _ : +". [V: fetched source] The AWS docs page on the shared files does not state the allowed characters. [V: fetched page text] botocore/AWS CLI may be more permissive (INI section names). [U]
  - A profile beginning with `-` could be read by the CLI's argparse as an option (e.g. `--profile --debug`); refusing a leading `-` avoids it. [U, reasoning]
- Windows: `shutil.which("aws")` may resolve to `aws.cmd`/`.bat` (pip-installed v1 does this); running a batch file goes through `cmd.exe` even with an argument list (the "BatBadBut" class of argument injection; `%` is an allowed profile character and expands in cmd). AWS CLI v2 installs `aws.exe`. [U — not run on Windows]
- botocore's default credential resolver drops the env provider when a profile is explicitly set ("An explicitly provided profile will negate an EnvProvider") — so env creds should not override `--profile` in the CLI (AWS CLI v2 vendors its own botocore). [V for installed botocore `credentials.py`; U for CLI v2]
- The CLI reads `~/.aws/` and SSO cache by design (R6.5 accepts this). [requirements]

## 7. Packaging facts

- boto3 1.40.0 pairs with botocore 1.40.x; datom code would only need `botocore` imports. R7.1 says the extra is boto3. [V versions installed; pairing range from pip resolution]
- pandas >= 2.0 also requires python-dateutil (>=2.8.2). [U — pandas metadata not read here]
- moto extra `moto[s3]` installs fine for 5.2.4 on 3.14. [V]

## 8. Open questions / risks spotted (for the design author)

1. **Region redirect vs R4.2.** botocore follows a server redirect to the bucket's real region (verified). R4.2 requires the given region. A `before-send` guard on the signing region refuses it (verified). Whether R/paws follows redirects is unknown — if it does, a Python reader would refuse a store R reads. Error to raise: probably `StoreUnreachable` naming the bucket's region.
2. **Network failure at connect.** R warns and continues; R2.3 says raise `StoreUnreachable`. Needs an explicit call.
3. **Log filter is process-wide.** Needed to satisfy R2.4 when a user turns on botocore DEBUG logging (token + access key id are logged today). Cost: removes those DEBUG lines for every botocore client in the process.
4. **Pickling.** Store must drop the client (unpicklable anyway) and pickle only bucket/prefix/region/function; a function that embeds secrets (e.g. `functools.partial` with keys, a dataclass holding keys) would carry them into the pickle — beyond datom's control. Alternative: refuse to pickle S3 stores at all (blocks multiprocessing use).
5. **repr.** Never call `repr()` on the user's function (a callable dataclass holding keys would leak); use its qualified name only. `vars(store)` will show the function object, whose repr is the user's.
6. **New error codes** beyond `access_denied`: credential result malformed, credential function failed / SSO failed, extra not installed. Codes are permanent once released.
7. **CA bundle.** The constant config store ignores `AWS_CA_BUNDLE` (and `ca_bundle` in config); `REQUESTS_CA_BUNDLE` and proxy env vars are still honoured by botocore. R4.3 allows honouring; decide whether to also honour `AWS_CA_BUNDLE` (env only).
8. **Destination host check.** Isolation is proven only for the ambient sources a test plants; a runtime check that each request goes over https to the region's AWS DNS suffix (from botocore's built-in endpoint data, `EndpointResolver.get_partition_dns_suffix()` / `get_partition_for_region()` exist in 1.43; not checked in 1.40) would stop the session token being sent to a non-AWS host if a future botocore adds a new override. Costs complexity; China/other partitions need the partition suffix, not a hard-coded `.amazonaws.com`.
9. **Op allow-list at runtime.** The store calls only three methods, but botocore can issue `CreateSession` itself (S3 Express, same client) — a `before-send` allow-list catches it; the test for AC2 alone would not.
10. **Private botocore surface.** `botocore.plugin` is documented as private; the component-replacement approach relies on undocumented behaviour. Tests that plant each source are what would catch a botocore change. Consider an upper bound or a CI job on the newest botocore.
11. **Credential function called twice on failure at connect** (governance probe swallows the first failure). Also, with < 15 min left on credentials, botocore calls the function on every request (for SSO: one `aws` subprocess per request). Accepted by decision 3, worth a README sentence.
12. **`list_artifacts()` reports access denied on the manifest as `manifest_unreadable`** (cause chained), because `read_manifest()` wraps all `DatomError`s, as R does. AC3 tests should target the store and `read()`/`get_set()`, or accept the wrapped form.
13. **Missing object with no `s3:ListBucket`** returns 403 on real S3, not 404 — but decision 1 (HeadBucket at connect needs ListBucket) means such a reader never gets a connection. moto returns 404 regardless.
14. **Already-expired credentials** returned by the function: botocore raises a bare `RuntimeError`; the datom wrapper should refuse first (same clock), leaving only a microsecond race.
15. **No-expiry credentials** need a far-future sentinel passed to botocore (`_set_from_data` cannot take None).
16. **Key namespace check.** S3 keys are literal (no `..` resolution by S3 [U]); a second-line check refusing `.`/`..` segments in keys would mirror the local store's `InvalidAddress` guard.
17. **Bucket argument.** R accepts any non-empty string; botocore validates bucket names at request time (`ParamValidationError`) and accepts ARNs. Decide whether to validate at construction and whether to refuse ARNs.
18. **Test hygiene.** moto's `mock_aws` may set fake `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars while active [U]; a "clean machine" test (AC5) should delete `AWS_*` vars and point `HOME`/`USERPROFILE` at an empty temp dir. `Loader.CUSTOMER_DATA_PATH` is fixed at botocore import, so tests must monkeypatch it. Planted endpoints should be caught by a test tripwire (`before-send` handler raising on an unexpected host) rather than letting botocore retry against a dead host (5 attempts with backoff — slow). Retry sleeps for connection-error tests need patching.
19. **AC10 test** needs a fresh interpreter (`subprocess` with `sys.modules['botocore'] = None` / `['boto3'] = None`) and a check that `import datom` leaves `botocore` out of `sys.modules`.
20. **Not verified at all:** Python 3.10–3.13 behaviour of the probes (all ran on 3.14.2); Windows; real AWS; paws/R behaviour for redirects and env vars; AWS CLI v2 exit codes and stderr text.
