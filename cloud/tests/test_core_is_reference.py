"""The engine in core/ must stay exactly what the build spec says it is.

docs/reference/BDPAY_BUILD_SPEC.md contains the reference source in full (part
10). This test extracts every file from it and compares it, byte for byte,
with core/. The self-host generator ships these files to users, and the hosted
service relies on their behaviour, so nobody edits them by accident.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "reference" / "BDPAY_BUILD_SPEC.md"


def reference_files() -> dict[str, str]:
    lines = SPEC.read_text(encoding="utf-8").split("\n")
    i = next(n for n, line in enumerate(lines) if line.startswith("## Part 10"))
    heading, fence = re.compile(r"^### `([^`]+)`$"), re.compile(r"^(`{3,})([\w+-]*)$")
    files: dict[str, str] = {}
    while i < len(lines):
        match = heading.match(lines[i])
        if not match:
            i += 1
            continue
        i += 1
        while not fence.match(lines[i]):
            i += 1
        ticks = fence.match(lines[i]).group(1)
        i += 1
        body = []
        while lines[i] != ticks:
            body.append(lines[i])
            i += 1
        files[match.group(1)] = "\n".join(body) + "\n"
    return files


def test_core_matches_the_build_spec_byte_for_byte():
    files = reference_files()
    assert len(files) == 45
    different = [path for path, text in files.items() if (ROOT / "core" / path).read_text(encoding="utf-8") != text]
    assert different == [], f"core/ differs from the build spec in: {different}"


def test_core_contains_nothing_the_spec_does_not_list():
    listed = set(reference_files()) | {"README.md"}  # the spec's tree lists a README without giving its text
    present = {
        p.relative_to(ROOT / "core").as_posix()
        for p in (ROOT / "core").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }
    assert present - listed == set(), f"files in core/ that the spec does not define: {sorted(present - listed)}"
