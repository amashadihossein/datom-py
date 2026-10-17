"""Show that the test suite fails when each read rule is deliberately broken.

For every break below: copy src/ to a temporary folder, apply the one edit, run
the suite against the copy, and expect it to FAIL. A break the suite doesn't
catch is reported, and the script exits non-zero.

    python tools/check_tests_catch_breaks.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (what the break does, file, exact text, replacement)
BREAKS = [
    ("skip the table hash check", "_table.py",
     'if parquet_sha is not None and parquet_sha != "":', "if False:"),
    ("parse table bytes before hashing them", "_table.py",
     "    raw = conn.store.read_bytes(key)\n", "    raw = conn.store.read_bytes(key)\n    pq.read_table(pa.BufferReader(raw))\n"),
    ("skip the set hash check", "_set.py", "if actual != document_sha:", "if False:"),
    ("parse set bytes before hashing them", "_set.py",
     "    actual = hashlib.sha256(raw).hexdigest()\n    if actual != document_sha:",
     "    json.loads(raw)\n    actual = hashlib.sha256(raw).hexdigest()\n    if actual != document_sha:"),
    ("tolerate a missing document_sha", "_set.py", "if not is_text_scalar(document_sha):", "if False:"),
    ("raise the format ceiling to 3", "_checks.py", "SUPPORTED_SCHEMA = 2", "SUPPORTED_SCHEMA = 3"),
    ("accept a non-whole format number", "_checks.py", "        and float(declared).is_integer()\n", ""),
    ("drop the kind check on table reads", "_table.py", '    check_kind(current, name, "table")\n', ""),
    ("treat an absent kind as any kind", "_checks.py", 'found = "table"', "found = expected"),
    ("skip the data-address check", "_table.py", '    validate_sha(data_sha, "data_sha")\n', ""),
    ("match versions exactly, not by prefix", "_metadata.py",
     'e["version"].startswith(version)', 'e["version"] == version'),
    ("take the first of several prefix matches", "_metadata.py", "    if len(matches) > 1:", "    if False:"),
    ("assume the newest history entry is current", "_metadata.py",
     "    return version if is_text_scalar(version) else None", '    return history[0].get("version")'),
    ("skip the older-manifest conversion", "_query.py", "    manifest = upgrade_manifest(manifest, declared)\n", ""),
    ("drop the governance warning", "_conn.py", "    if marker is not None:", "    if False:"),
    ("keep single labels as bare strings", "_set.py", "        return [v]\n", "        return v\n"),
    ("accept a member id field that isn't a string", "_set.py",
     "if not is_text_scalar(ident.get(field)):", "if ident.get(field) is None:"),
    ("ignore the tags filter when fetching a member", "_set.py",
     "        narrowed = [m for m in narrowed if _has_tags(m, tags)]", "        pass"),
    ("use pandas' default (lossy) integer conversion", "_table.py",
     "return table if as_arrow else to_pandas(table)", "return table if as_arrow else table.to_pandas()"),
    ("let a storage key escape the namespace", "_store.py",
     "if resolved != base and base not in resolved.parents:", "if False:"),
]


def main() -> int:
    missed = []
    for label, filename, old, new in BREAKS:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            shutil.copytree(ROOT / "src", src)
            target = src / "datom" / filename
            text = target.read_text()
            if text.count(old) != 1:
                print(f"SETUP ERROR  {label}: text not found exactly once in {filename}")
                missed.append(label)
                continue
            target.write_text(text.replace(old, new))
            env = {**os.environ, "PYTHONPATH": str(src)}
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", str(ROOT / "tests")],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
            caught = result.returncode != 0
            if not caught:
                missed.append(label)
            failed = [ln.split(" - ")[0].replace("FAILED ", "") for ln in result.stdout.splitlines() if ln.startswith("FAILED")]
            print(f"{'caught' if caught else 'MISSED':7s} {label:50s} {failed[0] if failed else ''}")
    print(f"\n{len(BREAKS) - len(missed)} of {len(BREAKS)} breaks caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
