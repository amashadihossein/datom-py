from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import datom

from tests.reader_cases import CASES_DIR


class EditableStore:
    """A writable copy of one case's storage, for unit tests that edit files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.datom = root / "datom"

    def path(self, key: str) -> Path:
        return self.datom.joinpath(*key.split("/"))

    def read_json(self, key: str):
        return json.loads(self.path(key).read_text())

    def write_json(self, key: str, doc) -> None:
        self.path(key).write_text(json.dumps(doc, indent=2))

    def conn(self, project: str = "STUDY_001") -> datom.Conn:
        return datom.get_conn(datom.store_local(self.root), project)

    def set_payload_key(self, name: str = "trial_product") -> str:
        sha = self.read_json(f"{name}/.metadata/metadata.json")["data_sha"]
        return f"{name}/{sha}.json"

    def replace_set_payload(self, payload, name: str = "trial_product") -> None:
        """Write a new payload and re-record its hash, so only the content check can refuse it."""
        key = self.set_payload_key(name)
        raw = json.dumps(payload, indent=2).encode()
        self.path(key).write_bytes(raw)
        meta_key = f"{name}/.metadata/metadata.json"
        meta = self.read_json(meta_key)
        meta["document_sha"] = hashlib.sha256(raw).hexdigest()
        self.write_json(meta_key, meta)


@pytest.fixture
def case_store(tmp_path):
    """``case_store("set-current")`` -> an editable copy of that case's storage."""

    def make(case: str) -> EditableStore:
        dest = tmp_path / case
        shutil.copytree(CASES_DIR / case / "storage", dest)
        return EditableStore(dest)

    return make
