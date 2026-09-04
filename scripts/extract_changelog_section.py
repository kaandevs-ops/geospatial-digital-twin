#!/usr/bin/env python3
"""CHANGELOG.md'den belirli bir sürümün bölümünü stdout'a yazar (stdlib-only).

`release.yml`, `git tag vX.Y.Z` push edildiğinde bu betiği çağırıp çıktısını
GitHub Release gövdesi olarak kullanır.

Kullanım::

    python scripts/extract_changelog_section.py 0.15.0
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def extract_section(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    # `## [0.15.0] - ...` başlığından bir sonraki `## [` başlığına (veya
    # dosya sonuna) kadar olan bloğu al.
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\].*?\n(.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"CHANGELOG.md içinde sürüm bulunamadı: {version!r}")
    return match.group(1).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("Kullanım: extract_changelog_section.py <version>", file=sys.stderr)
        return 2
    sys.stdout.write(extract_section(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
