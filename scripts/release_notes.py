#!/usr/bin/env python3
"""
release_notes.py - The notes for a draft release: CHANGELOG's entry for a tag, with what a reader needs to install it. It fails
if the tag and the app disagree or the entry is missing, so a mistyped tag stops before any CI minutes are spent.

    python scripts/release_notes.py v2.0.0

Standard library only, because .github/workflows/release.yml runs it before anything is installed.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def app_version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "src" / "atlas_toolkit" / "version.py").read_text(encoding="utf-8"))[1]


def changelog_entry(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    found = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return found[1].strip() if found else ""


def document(path: str, tag: str) -> str:
    server, repository = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY")
    return f"[{path}]({server}/{repository}/blob/{tag}/{path})" if server and repository else f"`{path}`"


def notes(tag: str) -> str:
    version = app_version()
    if tag != f"v{version}":
        sys.exit(f"the tag is {tag}, but this commit is version {version}: the tag must be v{version}")
    entry = changelog_entry(version)
    if not entry:
        sys.exit(f"CHANGELOG.md has no entry for {version}")
    rows = (("Windows 10 or 11, x64", f"AtlasToolkit-{version}-windows-x64-setup.exe", "docs/install/windows.md"),
            ("Ubuntu or Debian", f"atlas-toolkit_{version}_amd64.deb", "docs/install/linux.md"),
            ("Fedora or RHEL", f"atlas-toolkit-{version}-1.x86_64.rpm", "docs/install/linux.md"),
            ("Any Linux x86-64, glibc 2.34+", f"AtlasToolkit-{version}-linux-x86_64.AppImage", "docs/install/linux.md"),
            ("macOS 15+, Apple Silicon", f"AtlasToolkit-{version}-macos-arm64.dmg", "docs/install/macos.md"))
    table = "\n".join(f"| {system} | `{name}` | {document(guide, tag)} |" for system, name, guide in rows)
    return f"""{entry}

## Installing

| System | File | Instructions |
|---|---|---|
{table}

`SHA256SUMS.txt` holds each file's checksum. These installers are not signed by a trusted certificate, so Windows may warn and macOS asks for **Open Anyway**. {document("docs/PLATFORMS.md", tag)} says how far each system has been tested.
"""


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
