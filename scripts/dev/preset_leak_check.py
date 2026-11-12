#!/usr/bin/env python3
"""
preset_leak_check.py - Do any paid preset's values appear in this repository? Paid presets are sold in packs and never
belong in the public tree (docs/PRESETS.md). Reads the private packs through `[paths] presets` in dev.local.toml (or
ATLAS_PRESETS_DIR) and searches every tracked text file.

    python scripts/dev/preset_leak_check.py [--quiet] [--report FILE]

For each pack preset it takes the values that make it a look of its own: the keys its author set (`explicit`) whose
values differ from the tool's defaults. Basic-tier presets are free and do not count. Then:

  LEAK    (fails)  A file holds enough of one preset's own values together: `key=value`, `"key": value`, `key: value`
                   or `--flag value` within a few lines, or a workflow step / preset file / JSON object whose values
                   match. Values are weighed by how much they tell: a tuned number (38, 0.45, 2.1) counts 1, a round
                   one (0-10 in halves, multiples of 10, 25 or 50) or a choice counts 1/2, true/false nothing. Enough
                   means a score of 2.5 or more, at least 60% of the preset's own score, and at least one tuned
                   number among them (a preset of choices only must match all of four or more). Round values and
                   choices alone are what anyone would type, so a test that happens to use them is not a leak.
  REVIEW  (lists)  A line names a preset (bold, italic, quoted or in backticks), and it or a line next to it holds one
                   of the preset's tuned numbers. Prose such as "Fine detail (3 mm, joins 0.8 mm)" is found this way; decide by reading it.

A line that ends with the comment `preset-leak: ok` is skipped (for a value that only happens to match). Exit status:
0 clean, 1 leaks found, 2 no packs configured. tests/test_private_presets.py runs it when the packs are here.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

TEXT = {".py", ".md", ".txt", ".json", ".atlasflow", ".atlaspreset", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".iss",
        ".spec", ".bat", ".sh", ".html", ".csv"}
SKIP = ("tests/fixtures/presets/", "scripts/dev/preset_leak_check.py")    # made-up packs; this file
FREE_TIERS = {"basic"}
WINDOW = 12            # lines: values of one preset this close together count as one place
OK = "preset-leak: ok"


@dataclass
class Look:
    """One pack preset, reduced to the values that make it its own."""
    tool: str
    name: str
    uid: str
    own: dict                      # key -> JSON value, only what differs from the tool's defaults
    patterns: dict = field(default_factory=dict)    # key -> compiled regex for key=value forms
    numbers: set = field(default_factory=set)       # number tokens for the prose review

    @property
    def total(self) -> float:
        return sum(weight(v) for v in self.own.values())

    @property
    def tuned(self) -> int:
        return sum(1 for v in self.own.values() if weight(v) == 1)

    def leaks(self, keys) -> bool:
        """Do these matched keys give enough of this preset away?"""
        keys = set(keys)
        if not self.tuned:
            return len(self.own) >= 4 and keys >= set(self.own)
        score = sum(weight(self.own[k]) for k in keys)
        return (score >= 2.5 and score >= 0.6 * self.total
                and any(weight(self.own[k]) == 1 for k in keys))


@dataclass
class Finding:
    kind: str                      # LEAK or REVIEW
    path: str
    line: int
    look: Look
    keys: list
    note: str = ""

    def text(self) -> str:
        what = f"{self.look.tool} / {self.look.name}"
        if self.kind == "LEAK":
            return (f"LEAK    {self.path}:{self.line}  {what}: {len(self.keys)} of its {len(self.look.own)} own values "
                    f"({', '.join(self.keys)}){self.note}")
        return f"REVIEW  {self.path}:{self.line}  {what}: named with {', '.join(self.keys)}{self.note}"


# ---------------------------------------------------------------- values
def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _round(x: float) -> bool:
    return (abs(x) <= 10 and float(x * 2).is_integer()) or any(float(x / m).is_integer() for m in (10, 25, 50))


def weight(v) -> float:
    """How much a value tells about a look: a tuned number 1, a round number or a choice 1/2, true/false nothing."""
    if isinstance(v, bool) or v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return 0.5 if _round(float(v)) else 1.0
    if isinstance(v, (list, tuple)):
        return 1.0 if any(isinstance(x, (int, float)) and not _round(float(x)) for x in v) else 0.5
    return 0.5


def _number_tokens(v) -> list[str]:
    """How a number may be written: 0.45 -> 0.45, .45; 6.0 -> 6, 6.0; trailing zeros are allowed by the pattern."""
    if isinstance(v, bool):
        return []
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        i = int(v)
        return [str(i), f"{i}.0"]
    s = repr(float(v))
    if "e" in s:
        return [s, f"{v:g}"]
    out = [s]
    if s.startswith("0."):
        out.append(s[1:])
    elif s.startswith("-0."):
        out.append("-" + s[2:])
    return out


def _value_regex(v) -> str | None:
    if isinstance(v, bool):
        return "(?:True|true)" if v else "(?:False|false)"
    if isinstance(v, (int, float)):
        alts = "|".join(re.escape(t) for t in _number_tokens(v))
        return rf"(?:{alts})0*(?![\d\w]|\.\d)"
    if isinstance(v, str) and v:
        return re.escape(v) + r"(?![\w-])"
    return None                    # lists (vectors) and empty strings: only the JSON comparison sees them


def _pattern(key: str, v) -> re.Pattern | None:
    val = _value_regex(v)
    if val is None:
        return None
    dash = re.escape(key.replace("_", "-"))
    key = re.escape(key)
    return re.compile(rf"(?:(?<![\w-]){key}[\"']?\s*[:=]\s*[\"']?{val}"
                      rf"|--{dash}(?:\s+|=)[\"']?{val})")


def looks(packs, registry) -> list[Look]:
    from atlas_toolkit.core.params import FileRef
    out, seen = [], set()
    for pack in packs:
        if (pack.tier or "").lower() in FREE_TIERS:
            continue
        for p in pack.presets:
            tool = registry.tools.get(p.tool)
            if tool is None or p.uid in seen:
                continue
            seen.add(p.uid)
            defaults = tool.defaults()
            skip = set(tool.preset_skip) | {q.key for q in tool.param_list if isinstance(q, FileRef)}
            params = {q.key: q for q in tool.param_list}
            keys = [k for k in (p.explicit or p.values) if k in p.values and k in params and k not in skip]
            own = {}
            for k in keys:
                try:
                    default = params[k].to_json(defaults[k])
                except Exception:
                    default = defaults.get(k)
                if not _same(p.values[k], default):
                    own[k] = p.values[k]
            if len(own) < 2:       # one value alone is not a look anyone could take
                continue
            look = Look(p.tool, p.name, p.uid, own)
            look.patterns = {k: pat for k, v in own.items() if (pat := _pattern(k, v))}
            look.numbers = {_number_tokens(v)[0] for v in own.values()
                            if isinstance(v, (int, float)) and weight(v) == 1}
            out.append(look)
    return out


# ---------------------------------------------------------------- files
def tracked_files() -> list[Path]:
    names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split("\0")
    return [ROOT / n for n in names if n and Path(n).suffix.lower() in TEXT and not n.startswith(SKIP)]


def _json_objects(data):
    """Every dict in a JSON document that pairs a tool id with values (workflow steps, preset files) or just holds
    values, with the tool id when it names one."""
    stack = [data]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if isinstance(o.get("values"), dict):
                yield o.get("tool"), o["values"]
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)


def check_json(rel: str, text: str, all_looks: list[Look]) -> list[Finding]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    found = []
    for tool, values in _json_objects(data):
        for look in all_looks:
            if tool and tool != look.tool:
                continue
            hit = [k for k, v in look.own.items() if k in values and _same(values[k], v)]
            if look.leaks(hit):
                key = f'"{hit[0]}"'
                line = next((i for i, s in enumerate(text.splitlines(), 1) if key in s), 1)
                found.append(Finding("LEAK", rel, line, look, hit, " in a JSON step"))
    return found


def check_text(rel: str, lines: list[str], all_looks: list[Look]) -> list[Finding]:
    found = []
    blob = "\n".join(lines)
    for look in all_looks:
        live = {k: pat for k, pat in look.patterns.items() if k in blob or k.replace("_", "-") in blob}
        if not look.leaks(live):
            continue
        hits = []                                  # (line, key)
        for k, pat in live.items():
            for i, s in enumerate(lines, 1):
                if OK not in s and pat.search(s):
                    hits.append((i, k))
        if not look.leaks({k for _, k in hits}):
            continue
        hits.sort()
        for j, (start, _) in enumerate(hits):      # the most of its values within one window
            keys = {k for i, k in hits[j:] if i - start <= WINDOW}
            if look.leaks(keys):
                found.append(Finding("LEAK", rel, start, look, sorted(keys)))
                break
    return found


def check_prose(rel: str, lines: list[str], all_looks: list[Look]) -> list[Finding]:
    """A preset named (bold, italic, quoted or in backticks) with one of its tuned numbers on the same line or the
    lines just before and after it."""
    found = []
    by_name: dict[str, list[Look]] = {}
    for look in all_looks:
        by_name.setdefault(look.name, []).append(look)
    named = {name: re.compile(r"(?:\*\*|\*|_|`|\"|“|')" + re.escape(name) + r"(?:\*\*|\*|_|`|\"|”|')")
             for name in by_name}
    for i, s in enumerate(lines):
        for name, pat in named.items():
            if name not in s or not pat.search(s):
                continue
            near = " ".join(x for x in lines[max(0, i - 1):i + 2] if x.strip())
            if OK in near:
                continue
            for look in by_name[name]:
                nums = sorted(t for t in look.numbers if re.search(rf"(?<![\w.]){re.escape(t)}(?![\d\w]|\.\d)", near))
                if nums:
                    found.append(Finding("REVIEW", rel, i + 1, look, nums))
    return found


def scan(all_looks: list[Look], files: list[Path] | None = None) -> list[Finding]:
    findings = []
    for path in files if files is not None else tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        lines = text.splitlines()
        if path.suffix.lower() in {".json", ".atlasflow", ".atlaspreset"}:
            findings += check_json(rel, text, all_looks)
        else:
            findings += check_text(rel, lines, all_looks)
        if path.suffix.lower() in {".md", ".txt", ".html"}:
            findings += check_prose(rel, lines, all_looks)
    return findings


def load():
    import os
    os.environ.setdefault("ATLAS_TOOLKIT_HOME", str(ROOT / "build" / "qa" / "preset_leak_check" / "home"))
    from atlas_toolkit import devpaths
    from atlas_toolkit.core import presets as P
    from atlas_toolkit.core.registry import registry
    packs = [P.read_pack(folder) for folder in devpaths.preset_packs()]
    return packs, registry()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quiet", action="store_true", help="print leaks only, not the prose to review")
    ap.add_argument("--report", type=Path, help="also write the findings to this file")
    args = ap.parse_args()
    packs, reg = load()
    if not packs:
        print("No preset packs: set [paths] presets in dev.local.toml or ATLAS_PRESETS_DIR.")
        return 2
    all_looks = looks(packs, reg)
    findings = scan(all_looks)
    leaks = [f for f in findings if f.kind == "LEAK"]
    reviews = [f for f in findings if f.kind == "REVIEW"]
    shown = leaks + ([] if args.quiet else reviews)
    for f in shown:
        print(f.text())
    summary = (f"{len(all_looks)} paid presets checked in {len(packs)} pack(s): "
               f"{len(leaks)} leak(s), {len(reviews)} place(s) to review")
    print(summary)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join([f.text() for f in findings] + [summary]) + "\n", encoding="utf-8")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
