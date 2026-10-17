"""Unit tests, one group per read rule (amashadihossein/datom#129, "Read rules")."""

from __future__ import annotations

import copy
import warnings
from types import SimpleNamespace

import pandas as pd
import pytest

import datom
from datom import _set, _table
from datom._checks import check_kind, check_schema_version, validate_name, validate_sha
from datom._metadata import recorded_current_version, resolve_version
from datom._query import upgrade_manifest


# --- 1. storage layout ------------------------------------------------------------


def test_store_keys_live_under_prefix_datom(tmp_path):
    (tmp_path / "pre" / "datom" / "dm").mkdir(parents=True)
    (tmp_path / "pre" / "datom" / "dm" / "x.json").write_text("{}")
    store = datom.store_local(tmp_path, prefix="/pre/")
    assert store.exists("dm/x.json")
    assert not datom.store_local(tmp_path).exists("dm/x.json")


def test_store_refuses_a_key_that_escapes_the_namespace(tmp_path):
    (tmp_path / "datom").mkdir()
    (tmp_path / "secret.txt").write_text("no")
    with pytest.raises(datom.InvalidAddress):
        datom.store_local(tmp_path).read_bytes("../secret.txt")


def test_missing_store_folder_is_refused_and_not_created(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(datom.StoreUnreachable):
        datom.get_conn(datom.store_local(missing), "STUDY_001")
    assert not missing.exists()


def test_store_has_no_way_to_write():
    store = datom.store_local(".")
    for verb in ("write", "write_bytes", "upload", "put", "delete", "remove"):
        assert not hasattr(store, verb)


# --- 2. format check --------------------------------------------------------------


@pytest.mark.parametrize("doc, want", [({}, 1), ({"schema_version": None}, 1), ({"schema_version": 1}, 1),
                                        ({"schema_version": 2}, 2), ({"schema_version": 2.0}, 2)])
def test_schema_version_accepted(doc, want):
    assert check_schema_version(doc, "x") == want


def test_schema_version_newer_is_unsupported():
    with pytest.raises(datom.SchemaUnsupported):
        check_schema_version({"schema_version": 3}, "x")


@pytest.mark.parametrize("bad", [2.5, "2", True, [2], 0, -1, {"v": 2}])
def test_schema_version_malformed_is_invalid(bad):
    with pytest.raises(datom.SchemaInvalid):
        check_schema_version({"schema_version": bad}, "x")


# --- 3. kind check ----------------------------------------------------------------


def test_absent_kind_means_table():
    check_kind({"data_sha": "abcdef"}, "dm", "table")
    with pytest.raises(datom.KindMismatch):
        check_kind({"data_sha": "abcdef"}, "dm", "set")


def test_kind_mismatch_names_the_right_reader():
    with pytest.raises(datom.KindMismatch, match=r"datom\.get_set\(\)"):
        check_kind({"kind": "set"}, "trial_product", "table")


def test_kind_is_checked_before_the_data_path_is_built(case_store, monkeypatch):
    store = case_store("refuse-set-read-as-table")
    monkeypatch.setattr(_table, "read_parquet_verified", lambda *a, **k: pytest.fail("data path reached"))
    with pytest.raises(datom.KindMismatch):
        datom.read(store.conn(), "trial_product")


# --- 4. version -------------------------------------------------------------------

HISTORY = [
    {"version": "aa11", "data_sha": "d1", "timestamp": "t3"},
    {"version": "ab22", "data_sha": "d2", "timestamp": "t2"},
    {"version": "cc33", "data_sha": "d1", "timestamp": "t1"},
]


def test_version_by_unique_prefix_returns_the_full_recorded_version():
    r = resolve_version({}, HISTORY, "ab", "dm", "parquet_sha")
    assert (r.version, r.data_sha) == ("ab22", "d2")


def test_version_prefix_matching_two_is_ambiguous():
    with pytest.raises(datom.VersionAmbiguous):
        resolve_version({}, HISTORY, "a", "dm", "parquet_sha")


def test_version_not_in_history():
    with pytest.raises(datom.VersionNotFound):
        resolve_version({}, HISTORY, "ff", "dm", "parquet_sha")


def test_current_version_is_recorded_not_assumed_newest():
    # A write that reverts to older content appends nothing, so the current
    # document can describe an entry that isn't first.
    assert recorded_current_version({"data_sha": "d2"}, HISTORY) == "ab22"
    # Two entries share the data: the one whose timestamp matches created_at wins.
    assert recorded_current_version({"data_sha": "d1", "created_at": "t1"}, HISTORY) == "cc33"
    # No timestamp match: the first candidate.
    assert recorded_current_version({"data_sha": "d1", "created_at": "zz"}, HISTORY) == "aa11"
    assert recorded_current_version({"data_sha": "none"}, HISTORY) is None


def test_bad_version_argument():
    with pytest.raises(datom.InvalidArgument):
        resolve_version({}, HISTORY, "", "dm", "parquet_sha")


# --- 5. data address --------------------------------------------------------------


@pytest.mark.parametrize("good", ["abcdef", "0" * 64, "a3f8c1d2"])
def test_valid_address(good):
    assert validate_sha(good) == good


@pytest.mark.parametrize("bad", ["abcde", "0" * 65, "ABCDEF", "../x", "abcdef\n", "abcdeg", 123, None])
def test_invalid_address(bad):
    with pytest.raises(datom.InvalidAddress):
        validate_sha(bad)


@pytest.mark.parametrize("good", ["dm", "Lab results (v2)", "a-b_c"])
def test_valid_name(good):
    assert validate_name(good) == good


@pytest.mark.parametrize("bad", ["", "1dm", "dm/../x", ".metadata", "Datom", "x" * 129, None])
def test_invalid_name(bad):
    with pytest.raises(datom.InvalidName):
        validate_name(bad)


# --- 6. download, hash, then parse ------------------------------------------------


def test_tampered_table_is_refused_before_parsing(monkeypatch):
    from tests.test_reader_cases import CASES_DIR

    conn = datom.get_conn(datom.store_local(CASES_DIR / "refuse-table-integrity" / "storage"), "STUDY_001")
    monkeypatch.setattr(_table.pq, "read_table", lambda *a, **k: pytest.fail("tampered bytes were parsed"))
    with pytest.raises(datom.IntegrityMismatch):
        datom.read(conn, "dm")


def test_empty_parquet_sha_skips_the_check(case_store):
    store = case_store("table-current")
    meta = store.read_json("dm/.metadata/metadata.json")
    meta["parquet_sha"] = ""
    store.write_json("dm/.metadata/metadata.json", meta)
    assert datom.read(store.conn(), "dm").shape == (6, 4)


def test_tampered_set_payload_is_refused_before_parsing(case_store, monkeypatch):
    store = case_store("set-current")
    raw = store.path(store.set_payload_key()).read_bytes()
    store.path(store.set_payload_key()).write_bytes(raw + b" ")
    # Only the payload parse in the set module; metadata is parsed elsewhere.
    monkeypatch.setattr(_set, "json", SimpleNamespace(loads=lambda *a, **k: pytest.fail("tampered bytes were parsed")))
    with pytest.raises(datom.IntegrityMismatch):
        datom.get_set(store.conn(), "trial_product")


def test_set_with_empty_document_sha_is_an_error(case_store):
    store = case_store("set-current")
    meta = store.read_json("trial_product/.metadata/metadata.json")
    meta["document_sha"] = ""
    store.write_json("trial_product/.metadata/metadata.json", meta)
    with pytest.raises(datom.DocumentShaMissing):
        datom.get_set(store.conn(), "trial_product")


def test_pandas_keeps_integer64_exact_and_missing_values_missing():
    from tests.test_reader_cases import CASES_DIR

    conn = datom.get_conn(datom.store_local(CASES_DIR / "table-column-kinds" / "storage"), "STUDY_001")
    df = datom.read(conn, "kinds")
    assert isinstance(df, pd.DataFrame)
    assert int(df["i64"].iloc[0]) == 9007199254740993
    assert df["i64"].isna().tolist() == [False, True, False]
    assert str(df["int"].dtype) == "Int32" and str(df["lgl"].dtype) == "boolean"


# --- 7. sets ----------------------------------------------------------------------


def test_get_set_never_reads_member_data(case_store, monkeypatch):
    store = case_store("set-fetch-member")
    keys = []
    real = datom.LocalStore.read_bytes

    def spy(self, key):
        keys.append(key)
        return real(self, key)

    monkeypatch.setattr(datom.LocalStore, "read_bytes", spy)
    s = datom.get_set(store.conn(), "trial_product")
    assert len(s.members) == 3
    assert keys and all(k.startswith("trial_product/") for k in keys)


def test_labels_are_always_lists_of_strings(case_store):
    store = case_store("set-current")
    payload = store.read_json(store.set_payload_key())
    payload["tags"] = {"description": "one", "area": ["a", "b"]}
    store.replace_set_payload(payload)
    s = datom.get_set(store.conn(), "trial_product")
    assert s.tags == {"description": ["one"], "area": ["a", "b"]}


def test_untagged_member_has_no_tags(case_store):
    s = datom.get_set(case_store("set-current").conn(), "trial_product")
    lb = [m for m in s.members if m.id.name == "lb"][0]
    assert lb.tags is None


def _edit_first_member(store, edit):
    payload = store.read_json(store.set_payload_key())
    edit(payload["members"][0])
    store.replace_set_payload(payload)


@pytest.mark.parametrize(
    "edit",
    [
        lambda m: m["id"].update(version=42),
        lambda m: m["id"].pop("project"),
        lambda m: m["id"].update(name=["dm", "lb"]),
        lambda m: m["id"].update(kind=""),
        lambda m: m.pop("id"),
    ],
    ids=["version-number", "project-missing", "name-two-strings", "kind-empty", "no-id"],
)
def test_malformed_member_is_refused(case_store, edit):
    store = case_store("set-current")
    _edit_first_member(store, edit)
    with pytest.raises(datom.SetMemberMalformed):
        datom.get_set(store.conn(), "trial_product")


def test_one_element_string_array_in_an_id_is_read_as_that_string(case_store):
    store = case_store("set-current")
    _edit_first_member(store, lambda m: m["id"].update(name=["dm"]))
    assert datom.get_set(store.conn(), "trial_product").members[0].id.name == "dm"


@pytest.mark.parametrize("members", [None, {"dm": {}}, "dm"])
def test_members_must_be_a_list(case_store, members):
    store = case_store("set-current")
    payload = store.read_json(store.set_payload_key())
    payload["members"] = members
    store.replace_set_payload(payload)
    with pytest.raises(datom.SetPayloadMalformed):
        datom.get_set(store.conn(), "trial_product")


def test_member_that_is_a_set_is_not_followed_by_get_set(case_store):
    store = case_store("set-current")
    _edit_first_member(store, lambda m: m["id"].update(kind="set", name="other_product"))
    s = datom.get_set(store.conn(), "trial_product")  # other_product does not exist: never read
    assert s.members[0].id.kind == "set"


def test_fetch_member_by_member_object(case_store):
    store = case_store("set-fetch-member")
    conn = store.conn()
    s = datom.get_set(conn, "trial_product")
    current = [m for m in s.members if m.tags and m.tags.get("role") == ["current"]][0]
    assert current.fetch(conn).shape == datom.fetch_member(conn, s, current).shape
    with pytest.raises(datom.InvalidArgument):
        datom.fetch_member(conn, s, current, tags={"role": "current"})


def test_fetch_member_from_another_project_says_so(case_store):
    store = case_store("set-fetch-member")
    payload = store.read_json(store.set_payload_key())
    for m in payload["members"]:
        if m["id"]["name"] == "lb":
            m["id"]["project"] = "OTHER_STUDY"
    store.replace_set_payload(payload)
    conn = store.conn()
    s = datom.get_set(conn, "trial_product")
    with pytest.raises(datom.MemberProjectMismatch) as err:
        datom.fetch_member(conn, s, "lb")  # lb is not in this store
    assert isinstance(err.value.__cause__, datom.NotFound)


def test_unknown_member_kind(case_store):
    store = case_store("set-fetch-member")
    _edit_first_member(store, lambda m: m["id"].update(kind="model"))
    conn = store.conn()
    s = datom.get_set(conn, "trial_product")
    with pytest.raises(datom.MemberKindUnknown):
        s.members[0].fetch(conn)


# --- 8. older manifest format -----------------------------------------------------

V1 = {"project_name": "P", "tables": {"dm": {"current_version": "v"}, "x": {"kind": "set"}}}


def test_manifest_v1_converts_in_memory():
    original = copy.deepcopy(V1)
    m = upgrade_manifest(copy.deepcopy(V1), 1)
    assert "tables" not in m and m["schema_version"] == 2
    assert m["artifacts"]["dm"] == {"kind": "table", "current_version": "v"}
    assert m["artifacts"]["x"]["kind"] == "set"
    assert V1 == original


def test_manifest_current_is_untouched():
    m = {"schema_version": 2, "artifacts": {}}
    assert upgrade_manifest(m, 2) is m


def test_manifest_newer_is_refused(case_store):
    store = case_store("list")
    m = store.read_json(".metadata/manifest.json")
    m["schema_version"] = 3
    store.write_json(".metadata/manifest.json", m)
    with pytest.raises(datom.SchemaUnsupported):
        datom.list_artifacts(store.conn())


def test_manifest_without_an_artifact_list_stops_with_a_message(case_store):
    store = case_store("list")
    m = store.read_json(".metadata/manifest.json")
    del m["artifacts"]
    store.write_json(".metadata/manifest.json", m)
    with pytest.raises(datom.ManifestUnreadable, match="rebuilds"):
        datom.list_artifacts(store.conn())


def test_manifest_missing(case_store):
    store = case_store("list")
    store.path(".metadata/manifest.json").unlink()
    with pytest.raises(datom.ManifestUnreadable):
        datom.list_artifacts(store.conn())


def test_list_pattern_and_short_hash(case_store):
    df = datom.list_artifacts(case_store("list").conn(), pattern="d*")
    assert df["name"].tolist() == ["dm"]
    assert len(df["current_version"].iloc[0]) == 8


def test_empty_history_has_every_column(case_store):
    store = case_store("history-table")
    store.write_json("dm/.metadata/version_history.json", [])
    df = datom.history(store.conn(), "dm")
    assert list(df.columns) == ["version", "data_sha", "timestamp", "author", "commit_message", "commit_sha"]
    assert len(df) == 0


# --- 9. governance marker ---------------------------------------------------------


def test_invalid_governance_marker_does_not_warn(case_store):
    store = case_store("governance-marker")
    marker = store.read_json(".metadata/governance.json")
    del marker["gov_storage"]["root"]
    store.write_json(".metadata/governance.json", marker)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        store.conn()


def test_valid_governance_marker_warns(case_store):
    with pytest.warns(datom.GovernanceAttachedWarning, match="may go stale"):
        case_store("governance-marker").conn()


# --- 10. connections hold no secrets ----------------------------------------------


def test_conn_holds_only_store_and_project(tmp_path):
    (tmp_path / "datom").mkdir()
    conn = datom.get_conn(datom.store_local(tmp_path), "STUDY_001")
    assert set(vars(conn)) == {"store", "project_name"}
    assert set(vars(conn.store)) == {"root", "prefix"}
