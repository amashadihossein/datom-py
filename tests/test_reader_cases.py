"""Run every shared reader case through this package.

Each case under ``tests/reader-cases/`` is a store written by R datom plus the
result R datom produced on it (``case.json``). Python must read what R reads and
refuse what R refuses.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import datom
from datom._metadata import read_metadata, resolve_version
from datom._set import find_member

CASES_DIR = Path(__file__).parent / "reader-cases"
CASES = sorted(p for p in CASES_DIR.iterdir() if (p / "case.json").is_file())

PROJECT = "STUDY_001"

#: Cases this package can't pass yet, each with the reason. Never skip silently.
SKIPPED: dict[str, str] = {}


def arrow_type_name(t) -> str:
    """An Arrow type's name as R arrow prints it.

    R arrow drops ``ordered=0`` from dictionary types and spells ``ordered=1`` as
    ``ordered``; pyarrow prints Arrow C++'s own form. Everything else matches.
    """
    return str(t).replace(", ordered=0", "").replace("ordered=1", "ordered")


def conn_for(case_dir: Path) -> datom.Conn:
    return datom.get_conn(datom.store_local(case_dir / "storage"), PROJECT)


def table_result(case_dir: Path, conn: datom.Conn, name: str, version, table) -> dict:
    # The resolution R recorded, recomputed in Python...
    current, history = read_metadata(conn.store, name)
    resolved = resolve_version(current, history, version, name, "parquet_sha")
    # ...and proof that the table read came from exactly that data file.
    stored = pq.read_table(case_dir / "storage" / "datom" / name / f"{resolved.data_sha}.parquet")
    assert table.equals(stored), "the read returned a different data file than the one resolved"
    return {
        "ok": True,
        "version": resolved.version,
        "data_sha": resolved.data_sha,
        "nrow": table.num_rows,
        "ncol": table.num_columns,
        "colnames": table.column_names,
        "arrow_types": [arrow_type_name(f.type) for f in table.schema],
    }


def set_result(s: datom.DatomSet) -> dict:
    members = []
    for m in s.members:
        out = {"id": {"project": m.id.project, "name": m.id.name, "kind": m.id.kind, "version": m.id.version}}
        if m.tags is not None:
            out["tags"] = m.tags
        members.append(out)
    out = {
        "ok": True,
        "name": s.name,
        "project": s.project,
        "version": s.version,
        "data_sha": s.data_sha,
        "members": members,
    }
    if s.tags is not None:
        out["tags"] = s.tags
    return out


def frame_rows(df) -> list[dict]:
    return json.loads(df.to_json(orient="records"))


def run_call(case_dir: Path, call: dict) -> dict:
    conn = conn_for(case_dir)
    op = call["op"]
    if op == "read_table":
        table = datom.read(conn, call["name"], version=call["version"], as_arrow=True)
        return table_result(case_dir, conn, call["name"], call["version"], table)
    if op == "read_set":
        return set_result(datom.get_set(conn, call["name"], version=call["version"]))
    if op == "fetch_member":
        s = datom.get_set(conn, call["set"], version=call["set_version"])
        table = datom.fetch_member(conn, s, call["member"], tags=call["tags"], version=call["version"], as_arrow=True)
        record = find_member(s.members, call["member"], call["tags"], call["version"])
        return table_result(case_dir, conn, record.id.name, record.id.version, table)
    if op == "history":
        return {"ok": True, "rows": frame_rows(datom.history(conn, call["name"], n=call["n"]))}
    if op == "list":
        return {"ok": True, "rows": frame_rows(datom.list_artifacts(conn, include_versions=True, short_hash=False))}
    raise AssertionError(f"unknown op {op!r}")


@pytest.mark.parametrize("case_dir", CASES, ids=[p.name for p in CASES])
def test_reader_case(case_dir: Path) -> None:
    if case_dir.name in SKIPPED:
        pytest.skip(SKIPPED[case_dir.name])

    case = json.loads((case_dir / "case.json").read_text())
    call, expect = case["call"], case["expect"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if not expect["ok"]:
            with pytest.raises(datom.DatomError) as err:
                run_call(case_dir, call)
            assert err.value.code == expect["error"], str(err.value)
            return
        got = run_call(case_dir, call)

    codes = sorted({w.message.code for w in caught if hasattr(w.message, "code")})
    assert codes == sorted(expect.get("warnings", []))

    want = {k: v for k, v in expect.items() if k != "warnings"}
    assert got == want


def test_every_case_is_run() -> None:
    # A typo in a folder name, or a case without case.json, would otherwise
    # drop out of the suite without anyone noticing.
    folders = sorted(p.name for p in CASES_DIR.iterdir() if p.is_dir())
    assert folders == [p.name for p in CASES]
    assert len(CASES) >= 28


def test_reading_every_case_writes_nothing() -> None:
    def snapshot() -> dict:
        return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in CASES_DIR.rglob("*") if p.is_file()}

    before = snapshot()
    for case_dir in CASES:
        case = json.loads((case_dir / "case.json").read_text())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                run_call(case_dir, case["call"])
            except datom.DatomError:
                pass
    assert snapshot() == before
