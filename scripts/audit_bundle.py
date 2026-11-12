#!/usr/bin/env python3
"""
audit_bundle.py - Would this build run on the systems it promises, and is everything in it? Reads the built folder or .app
without starting anything, so a problem shows on the build machine and not on somebody else's.

    python scripts/audit_bundle.py PATH [--report FILE]

Every OS: the four helpers, their notices, the app's LICENSE and the GPL source archive are there, the helpers can be
executed, and libigl's copyleft and Triangle modules are not in the bundle.
Linux: no file needs a glibc newer than the floor, helpers need no bundled C++ runtime, and the libraries Mesa needs from
the system are not in the bundle. macOS: every file runs on Apple Silicon and on the minimum macOS, and the signature is
valid. Standard library only; it uses objdump on Linux, and vtool, lipo and codesign on macOS.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

GLIBC_FLOOR = (2, 34)
MACOS_FLOOR = (15, 0)
HELPERS = ("texture", "stripes", "remesh", "developable")
NOTICES = ("surface-texture.txt", "surface-stripes.txt", "remesh-decimate.txt", "developable-surfaces.txt",
           "developable-source.zip", "THIRD_PARTY_NOTICES.md")
LEFT_OUT = ("/igl/copyleft/", "/igl/triangle/")    # CGAL (GPL-3.0), TetGen (AGPL-3.0), Triangle (not open source)
FROM_THE_SYSTEM = ("libstdc++.so", "libgcc_s.so", "libz.so", "libzstd.so", "libexpat.so", "liblzma.so")
ROOT = Path(__file__).resolve().parents[1]
# PySide6 6.12's own wheels are tagged macOS 14, yet these libraries say minos 26.0. dyld does not enforce that field for
# libraries, and CI runs them on macOS 15 through the frozen self-test. Noted, not a problem. Anything else above the floor is.
MARKED_TOO_HIGH_UPSTREAM = ("/PySide6/", "/shiboken6/")


def run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, errors="replace").stdout


def version_tuple(text: str) -> tuple:
    return tuple(int(x) for x in text.split("."))


class Audit:
    def __init__(self, root: Path):
        self.root, self.problems, self.lines = root, [], []
        self.files = sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())

    def say(self, text: str) -> None:
        self.lines.append(text)
        print(text, flush=True)

    def problem(self, text: str) -> None:
        self.problems.append(text)
        self.say(f"PROBLEM {text}")

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def common(self, windows: bool) -> None:
        suffix = ".exe" if windows else ""
        found = {p.name: p for p in self.files}
        for helper in HELPERS:
            exe = found.get(f"atlas-{helper}-core{suffix}")
            if exe is None:
                self.problem(f"helper missing: atlas-{helper}-core{suffix}")
            elif not windows and not exe.stat().st_mode & 0o100:
                self.problem(f"helper is not executable: {self.rel(exe)}")
        for notice in NOTICES:
            if notice not in found:
                self.problem(f"notice or source archive missing: {notice}")
        if "LICENSE" not in found:
            self.problem("the app's own LICENSE is missing")
        for p in self.files:          # libigl modules under other licences (packaging/atlas_toolkit.spec, LEFT_OUT)
            if any(part in f"/{self.rel(p)}" for part in LEFT_OUT):
                self.problem(f"left-out libigl module is in the bundle: {self.rel(p)}")
        self.say(f"files: {len(self.files)}, {sum(p.stat().st_size for p in self.files) / 1e6:.0f} MB")

    def linux(self) -> None:
        if not shutil.which("objdump"):
            self.problem("objdump is missing (install binutils): the glibc floor was not checked")
            return
        elf = [p for p in self.files if p.open("rb").read(4) == b"\x7fELF"]
        highest, worst = (0, 0), None
        for path in elf:
            symbols = run("objdump", "-T", str(path))
            for version in set(re.findall(r"GLIBC_([0-9]+\.[0-9]+)", symbols)):
                found = version_tuple(version)
                if found > highest:
                    highest, worst = found, path
                if found > GLIBC_FLOOR:
                    self.problem(f"needs GLIBC_{version}, above the floor: {self.rel(path)}")
            if path.name.startswith("atlas-") and path.name.endswith("-core") and "GLIBCXX_" in symbols:
                self.problem(f"helper depends on the system's libstdc++ version: {self.rel(path)}")
            if path.parent.name == "_internal" and path.name.startswith(FROM_THE_SYSTEM):
                self.problem(f"must come from the system, or Mesa cannot load: {self.rel(path)}")
        self.say(f"ELF files: {len(elf)}; highest glibc needed: {'.'.join(map(str, highest))} ({worst.name if worst else ''})")
        libraries = sorted(p.name for p in elf if p.parent.name == "_internal" and p.name.startswith("lib"))
        self.say(f"system libraries bundled ({len(libraries)}): {' '.join(libraries)}")

    def macos(self) -> None:
        macho = [p for p in self.files if p.open("rb").read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")]
        highest, upstream = (0, 0), 0
        for path in macho:
            if "arm64" not in run("lipo", "-archs", str(path)).split():
                self.problem(f"has no Apple Silicon code: {self.rel(path)}")
            match = re.search(r"minos\s+([0-9]+\.[0-9]+)", run("vtool", "-show-build", str(path)))
            if match:
                found = version_tuple(match.group(1))
                highest = max(highest, found)
                if found > MACOS_FLOOR:
                    if any(part in "/" + self.rel(path) for part in MARKED_TOO_HIGH_UPSTREAM):
                        upstream += 1
                    else:
                        self.problem(f"needs macOS {match.group(1)}, above the floor: {self.rel(path)}")
        self.say(f"Mach-O files: {len(macho)}; highest minimum macOS: {'.'.join(map(str, highest))}, "
                 f"{upstream} of them PySide6 libraries marked above the floor upstream")
        if self.root.suffix == ".app":
            done = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(self.root)], capture_output=True, text=True)
            if done.returncode:
                self.problem(f"the signature does not verify: {done.stderr.strip()[:300]}")
            else:
                self.say("signature verifies")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    if not a.path.is_dir():
        sys.exit(f"not a folder or an app: {a.path} (a one-file build cannot be audited: build with --onedir)")
    audit = Audit(a.path)
    audit.common(sys.platform == "win32")
    if sys.platform == "darwin":
        audit.macos()
    elif sys.platform != "win32":
        audit.linux()
    audit.say(f"{'clean' if not audit.problems else str(len(audit.problems)) + ' problem(s)'}: {a.path.name}")
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("\n".join(audit.lines) + "\n", encoding="utf-8")
    return 1 if audit.problems else 0


if __name__ == "__main__":
    sys.exit(main())
