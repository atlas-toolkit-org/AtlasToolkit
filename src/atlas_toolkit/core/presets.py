"""
Presets are data, never code: named sets of a tool's settings, kept in files the user can save, share and load.

Two file formats, both JSON (docs/PRESETS.md has the full specification):

    .atlaspreset   one or more presets, for any tools: a user's own presets, exported or imported
    .atlaspack     a preset pack: a zip (or, while it is being made, a folder) holding pack.json,
                   presets/<tool-id>.json and signature.json, one tier of a collection ("Atlas Presets 2027 ·
                   Essentials"). One file holds everything to install and check it, the signer's public key too.
                   A .zip of the pack's folder, or a .zip holding packs and preset files, loads as well.

Where they live (core/paths.py):

    <config>/presets/mine/<tool-id>.atlaspreset   the user's own presets, one file per tool
    <config>/presets/hidden.json                   pack presets the user has hidden
    <data>/packs/*.atlaspack                       installed packs (copied here when loaded)
    ATLAS_TOOLKIT_PRESET_DIRS                      more pack files or pack folders (os.pathsep-separated)

A preset stores its values as a full snapshot in the Param.to_json form, plus the tool version that wrote them.
Reading tolerates schema change: renamed keys and values come through `former` / `former_values` on the Param,
an optional `Tool.migrate` hook handles the rest, and whatever still does not fit falls back to the default and is
reported (apply() returns those problems) instead of breaking the page.

No Qt here: the CLI, the worker and tests use it too. ui/context.py wraps the library with a change signal.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .params import AUTO, FileRef, Params, resolve_lenient

FORMAT = "atlas-toolkit.preset"
PACK_FORMAT = "atlas-toolkit.pack"
FORMAT_VERSION = 1
EXT = ".atlaspreset"
PACK_EXT = ".atlaspack"
PACK_MANIFEST = "pack.json"
SIGNATURE = "signature.json"
SIGN_CONTEXT = b"atlas-toolkit.pack.v1\n"          # what is signed: this, then the exact pack.json bytes
ENV_DIRS = "ATLAS_TOOLKIT_PRESET_DIRS"
MINE = "mine"
MINE_TITLE = "My presets"

# A pack is read in memory; these keep a hostile or broken zip from exhausting it.
MAX_ENTRIES = 5000
MAX_TOTAL = 200 * 1024 * 1024
MAX_ENTRY = 32 * 1024 * 1024
PACK_SUFFIXES = {".json", ".png", ".webp", ".jpg", ".jpeg", ".txt", ".md"}
TIER_RANKS = {"basic": 10, "essentials": 20, "complete": 30}
STATUS_LABELS = {          # a pack's verification, as people read it
    "official": "Official",
    "trusted": "Trusted publisher",
    "signed": "Signed, publisher not trusted",
    "modified": "Modified",
    "unsigned": "Unsigned",
    "unknown key": "Unknown publisher key",
    "revoked": "Key revoked",
}
STATUS_RANK = {"official": 3, "trusted": 2, "signed": 1}   # which copy of a preset wins; everything else is 0
ENDORSE_CONTEXT = b"atlas-toolkit.key.v1\n"     # what a trusted key signs to vouch for a newer key
ZIP_SUFFIX = ".zip"
INSTALLABLE = (EXT, PACK_EXT, ZIP_SUFFIX)       # what "Load presets or a pack" takes (a .zip only if it holds them)
MAX_BUNDLE = 256 * 1024 * 1024                  # a downloaded .zip holding packs: larger is not one

_KNOWN = ("uid", "name", "tool", "tool_version", "values", "explicit", "tip", "tags", "created", "modified",
          "based_on")


class PresetFormatError(ValueError):
    """A file that is not a preset file or pack, or that a newer Atlas Toolkit wrote."""


# ---------------------------------------------------------------------- versions
def version_tuple(v: str) -> tuple:
    """'1.10.2' -> (1, 10, 2), padded so '1.1' == '1.1.0'; non-numeric parts sort after numbers."""
    parts = []
    for piece in re.split(r"[.\-+]", str(v or "").strip()):
        if piece.isdigit():
            parts.append((0, int(piece)))
        elif piece:
            parts.append((1, piece))
    while parts and parts[-1] == (0, 0):
        parts.pop()
    return tuple(parts)


def older(a: str, b: str) -> bool:
    """True when version `a` comes before `b`. An unknown version ('') counts as older than any."""
    return version_tuple(a) < version_tuple(b)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug(name: str) -> str:
    """A stable, ASCII identifier from a display name: 'Möbius Wreath' -> 'mobius-wreath', '2 × 2' -> '2-x-2'."""
    text = unicodedata.normalize("NFKD", str(name).replace("×", " x ")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "preset"


def _write_json(path: Path, data) -> None:
    """Atomic write: a crash never leaves half a preset file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _toolkit_version() -> str:
    from ..version import __version__
    return __version__


# ---------------------------------------------------------------------- one preset
@dataclass
class Preset:
    name: str
    tool: str
    values: dict                                 # Param.to_json form; keys a tool no longer has are kept
    uid: str = ""
    tool_version: str = ""                       # the tool version that wrote the values ("" = unknown)
    explicit: tuple = ()                         # the keys the author set on purpose (the rest are defaults)
    tip: str = ""
    tags: tuple = ()
    created: str = ""
    modified: str = ""
    based_on: str = ""                           # uid of the preset this one was duplicated from
    extra: dict = field(default_factory=dict)    # fields this version does not know, kept for round trips
    source: str = MINE                           # runtime only: MINE or a pack's collection
    pack: "Pack | None" = None                   # runtime only: the pack it came from

    def __post_init__(self):
        if not self.uid:
            self.uid = f"{MINE}/{self.tool}/{uuid.uuid4().hex[:12]}"
        self.explicit = tuple(self.explicit)
        self.tags = tuple(self.tags)

    @property
    def mine(self) -> bool:
        return self.source == MINE

    def to_dict(self) -> dict:
        d = {"uid": self.uid, "name": self.name, "tool": self.tool, "tool_version": self.tool_version,
             "values": self.values}
        for k in ("explicit", "tip", "tags", "created", "modified", "based_on"):
            v = getattr(self, k)
            if v:
                d[k] = list(v) if isinstance(v, tuple) else v
        d.update({k: v for k, v in self.extra.items() if k not in d})
        return d

    @classmethod
    def from_dict(cls, d: dict, source: str = MINE, pack=None) -> "Preset":
        if not isinstance(d, dict) or not isinstance(d.get("values"), dict) or not d.get("name") or not d.get("tool"):
            raise PresetFormatError("a preset needs a name, a tool and its values")
        return cls(name=str(d["name"]), tool=str(d["tool"]), values=dict(d["values"]), uid=str(d.get("uid") or ""),
                   tool_version=str(d.get("tool_version") or ""), explicit=tuple(d.get("explicit") or ()),
                   tip=str(d.get("tip") or ""), tags=tuple(d.get("tags") or ()), created=str(d.get("created") or ""),
                   modified=str(d.get("modified") or ""), based_on=str(d.get("based_on") or ""),
                   extra={k: v for k, v in d.items() if k not in _KNOWN}, source=source, pack=pack)


def snapshot(tool, values: dict, inputs_too: bool = True, explicit: Iterable | None = None) -> dict:
    """The JSON values a preset stores for `values`: every parameter except view state (`preset_skip`) and second
    input files (paths belong to one computer). With inputs_too=False, `preset_inputs` keys are left out unless they
    are in `explicit`, so applying the preset keeps the user's input settings."""
    skip = set(tool.preset_skip)
    keep_inputs = set(explicit or ())
    out = {}
    for p in tool.param_list:
        if p.key not in values or p.key in skip or isinstance(p, FileRef):
            continue
        if not inputs_too and p.key in tool.preset_inputs and p.key not in keep_inputs:
            continue
        v = values[p.key]
        out[p.key] = v if (isinstance(v, str) and v == AUTO) else p.to_json(v)
    return out


def _protected(tool, preset: Preset) -> set:
    """Keys applying `preset` never changes: view state, second input files, and the input settings it does not set."""
    keys = set(tool.preset_skip) | {p.key for p in tool.param_list if isinstance(p, FileRef)}
    return keys | (set(tool.preset_inputs) - set(preset.values))


def apply(tool, preset: Preset, current: dict | None = None) -> tuple[Params, list[str]]:
    """The values `preset` gives `tool`, and what did not fit (problems, in words; empty when it applies cleanly).
    The preset goes on top of the tool's defaults; with `current` (the form's values), view state, second input
    files and the input settings the preset does not set keep their current values."""
    raw = dict(preset.values)
    if getattr(tool, "migrate", None):
        raw = tool.resolve(tool.migrate)(dict(raw), preset.tool_version) or raw
    base = tool.defaults()
    merged = dict(base)
    merged.update(raw)
    values, problems = resolve_lenient(tool.param_list, merged)
    unknown = [k for k in raw if k not in base and not any(k in p.former for p in tool.param_list)]
    problems = [p for p in problems if not p.startswith("no longer used")]
    if unknown:
        problems.append(f"no longer used: {', '.join(sorted(unknown))}")
    if current is not None:
        for k in _protected(tool, preset):
            if k in current:
                values[k] = current[k]
    return values, problems


def _same(a, b) -> bool:
    from .tool import _same as same
    return same(a, b)


_MATCH_CACHE: dict = {}


def _applied(tool, preset: Preset) -> Params:
    key = (tool.id, tool.version, preset.uid, preset.modified, json.dumps(preset.values, sort_keys=True, default=str))
    got = _MATCH_CACHE.get(key)
    if got is None:
        if len(_MATCH_CACHE) > 4000:
            _MATCH_CACHE.clear()
        got = _MATCH_CACHE[key] = apply(tool, preset)[0]
    return got


def matches(tool, values: dict, preset: Preset) -> bool:
    """True when `values` are what applying `preset` gives (keys a preset never changes do not count)."""
    pv = _applied(tool, preset)
    protected = _protected(tool, preset)
    return all(_same(values.get(k), v) for k, v in pv.items() if k not in protected)


def match(tool, values: dict, presets: Iterable[Preset]) -> Preset | None:
    """The first of `presets` that `values` match, or None."""
    for p in presets:
        try:
            if matches(tool, values, p):
                return p
        except Exception:        # a preset that no longer fits never breaks a page
            continue
    return None


# ---------------------------------------------------------------------- .atlaspreset files
def _check_format(data, expected: str, what: str) -> None:
    if not isinstance(data, dict) or data.get("format") != expected:
        raise PresetFormatError(f"not an Atlas Toolkit {what}")
    try:
        version = int(data.get("format_version", 1))
    except (TypeError, ValueError):
        raise PresetFormatError(f"this {what} has an unreadable format version") from None
    if version > FORMAT_VERSION:
        raise PresetFormatError(f"this {what} was made by a newer Atlas Toolkit (format {version}); update the app")


def parse_preset_data(data, source: str = MINE, pack=None) -> list[Preset]:
    _check_format(data, FORMAT, "preset file")
    items = data.get("presets")
    if not isinstance(items, list):
        raise PresetFormatError("the preset file lists no presets")
    return [Preset.from_dict(d, source, pack) for d in items]


def read_preset_file(path: Path, source: str = MINE) -> list[Preset]:
    """The presets in an .atlaspreset file. PresetFormatError when it is not one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise PresetFormatError(f"{Path(path).name} could not be read: {e}") from None
    return parse_preset_data(data, source)


def preset_file_data(presets: Iterable[Preset]) -> dict:
    return {"format": FORMAT, "format_version": FORMAT_VERSION, "toolkit": _toolkit_version(),
            "presets": [p.to_dict() for p in presets]}


def write_preset_file(path: Path, presets: Iterable[Preset]) -> Path:
    path = Path(path)
    if path.suffix.lower() != EXT:
        path = path.with_suffix(EXT)
    _write_json(path, preset_file_data(presets))
    return path


# ---------------------------------------------------------------------- packs
@dataclass
class Pack:
    collection: str              # "atlas-presets-2027": tiers of one collection share uids and one heading
    name: str                    # "Atlas Presets 2027"
    tier: str = "complete"
    tier_rank: int = 30
    pack_version: str = "1"
    edition: str = ""
    publisher: str = ""
    toolkit_min: str = ""
    licence: str = ""
    supersedes: tuple = ()
    path: Path | None = None
    presets: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    status: str = "unsigned"     # official / modified / unsigned / unknown key / revoked (STATUS_LABELS)
    problems: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.collection}:{self.tier}"

    @property
    def title(self) -> str:
        return f"{self.name} · {self.tier.capitalize()}" if self.tier else self.name

    signer: "Signer | None" = None   # who signed it, when the signature holds (Official, trusted or not)

    @property
    def verified(self) -> bool:
        return self.status == "official"

    @property
    def rank(self) -> int:
        return STATUS_RANK.get(self.status, 0)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def signed_by(self) -> str:
        """The publisher whose key verified this pack ('' when the signature does not hold)."""
        return self.signer.publisher if self.signer and self.rank else ""


@dataclass(frozen=True)
class Signer:
    """The key a pack was signed with, as the pack itself carries it."""
    key_id: str
    public: str                  # hex
    publisher: str

    @property
    def fingerprint(self) -> str:
        """What a person compares with the publisher's website: 16 hex digits in 4 groups."""
        h = hashlib.sha256(bytes.fromhex(self.public)).hexdigest()[:16].upper()
        return " ".join(h[i:i + 4] for i in range(0, 16, 4))


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return (not name.startswith(("/", "\\")) and ":" not in name and ".." not in p.parts and "\\" not in name
            and (name.endswith("/") or p.suffix.lower() in PACK_SUFFIXES or name == "signature.json"))


def _zip_entries(data, label: str, limit: int = MAX_TOTAL) -> dict[str, bytes]:
    """Every file in a zip (a path or bytes), checked for size and count before anything is read."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data)
    except (OSError, zipfile.BadZipFile) as e:
        raise PresetFormatError(f"{label} is not a zip file: {e}") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PresetFormatError(f"{label} has too many entries")
        if sum(i.file_size for i in infos) > limit:
            raise PresetFormatError(f"{label} is too large to hold presets")
        files = {}
        for info in infos:
            if info.is_dir() or info.filename.endswith("/"):
                continue
            if info.file_size > MAX_ENTRY and not info.filename.lower().endswith(PACK_EXT):
                continue
            try:
                files[info.filename] = zf.read(info)
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:   # damaged, or encrypted
                raise PresetFormatError(f"{label}: cannot read {info.filename}: {e}") from None
        return files


def _pack_root(files: dict[str, bytes]) -> str | None:
    """Where pack.json sits in a zip: '' at the top, 'folder/' when someone zipped the pack's folder, None if
    nowhere. A pack zipped by hand from its folder keeps its signature: every file's bytes are unchanged."""
    if PACK_MANIFEST in files:
        return ""
    tops = {n.split("/", 1)[0] for n in files if not n.startswith("__MACOSX/")}
    if len(tops) == 1:
        top = next(iter(tops)) + "/"
        if top + PACK_MANIFEST in files:
            return top
    return None


def _pack_files(files: dict[str, bytes], label: str) -> dict[str, bytes]:
    """A pack's own files out of a zip's entries (the root folder taken off), every name checked."""
    root = _pack_root(files)
    if root is None:
        raise PresetFormatError(f"{label} is not a preset pack (no {PACK_MANIFEST})")
    out = {}
    for name, data in files.items():
        if name.startswith("__MACOSX/") or PurePosixPath(name).name in (".DS_Store", "Thumbs.db"):
            continue
        if not name.startswith(root):
            raise PresetFormatError(f"{label} holds files beside the pack: {name}")
        rel = name[len(root):]
        if not _safe_member(rel):
            raise PresetFormatError(f"{label} holds an unexpected entry: {name}")
        if len(data) > MAX_ENTRY:
            raise PresetFormatError(f"{label}: {name} is too large")
        out[rel] = data
    return out


def _read_folder(path: Path) -> dict[str, bytes]:
    files = {}
    for f in sorted(path.rglob("*")):
        if f.is_file() and not any(part.startswith(".") for part in f.relative_to(path).parts):
            rel = f.relative_to(path).as_posix()
            if PurePosixPath(rel).suffix.lower() in PACK_SUFFIXES:
                files[rel] = f.read_bytes()
    return files


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    """Files as a zip with entries in name order and fixed timestamps: the same files always give the same bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    return buf.getvalue()


def read_pack(path: Path, trusted: dict | None = None) -> Pack:
    """A pack from an .atlaspack file (or a .zip of the pack's folder) or a pack source folder. `trusted` holds the
    fingerprints of publishers the user trusts (PresetLibrary.trusted()). PresetFormatError when it is not one."""
    path = Path(path)
    if path.is_dir():
        files = _read_folder(path)
    else:
        files = _pack_files(_zip_entries(path, path.name), path.name)
    return pack_from_files(files, path.name, path, trusted)


def pack_from_files(files: dict[str, bytes], label: str, path: Path | None = None,
                    trusted: dict | None = None) -> Pack:
    if PACK_MANIFEST not in files:
        raise PresetFormatError(f"{label} is not a preset pack (no {PACK_MANIFEST})")
    try:
        meta = json.loads(files[PACK_MANIFEST].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PresetFormatError(f"{label}: unreadable {PACK_MANIFEST}: {e}") from None
    _check_format(meta, PACK_FORMAT, "preset pack")
    collection = str(meta.get("collection") or "").strip()
    if not collection or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", collection):
        raise PresetFormatError(f"{label}: the pack needs a collection id (lower case, digits, - . _)")
    tier = str(meta.get("tier") or "complete").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", tier):
        raise PresetFormatError(f"{label}: the tier must be a plain word (lower case, digits, - _)")
    pack = Pack(collection=collection, name=str(meta.get("name") or collection), tier=tier,
                tier_rank=int(meta.get("tier_rank") or TIER_RANKS.get(tier, 0)),
                pack_version=str(meta.get("pack_version") or "1"), edition=str(meta.get("edition") or ""),
                publisher=str(meta.get("publisher") or ""), toolkit_min=str(meta.get("toolkit_min") or ""),
                licence=str(meta.get("licence") or ""), supersedes=tuple(meta.get("supersedes") or ()),
                path=path, meta=meta)
    pack.status, pack.signer, why = _verification(files, meta, trusted or {})
    pack.problems.extend(why)
    if pack.toolkit_min and older(_toolkit_version(), pack.toolkit_min):
        pack.problems.append(f"made for Atlas Toolkit {pack.toolkit_min} or later")
    for name in sorted(n for n in files if n.startswith("presets/") and n.endswith(".json")):
        try:
            items = parse_preset_data(json.loads(files[name].decode("utf-8")), collection, pack)
        except (PresetFormatError, UnicodeDecodeError, ValueError) as e:
            pack.problems.append(f"{name}: {e}")
            continue
        for p in items:
            if not p.uid or p.uid.startswith(f"{MINE}/"):
                p.uid = f"{collection}/{p.tool}/{slug(p.name)}"
            pack.presets.append(p)
    return pack


def _endorsed(endorsement, key_id: str, public: str) -> str | None:
    """The publisher a built-in key vouches for, when `endorsement` is a valid statement by one of this app's keys
    that `key_id` = `public` belongs to that publisher; None otherwise. This is how a pack carries everything needed
    to verify it after the publisher moves to a new key: the old, built-in key signed the new one."""
    from . import ed25519
    from .preset_keys import KEYS
    if not isinstance(endorsement, dict):
        return None
    root = KEYS.get(str(endorsement.get("by", "")))
    if root is None or root.status == "revoked":
        return None
    claim = {"key_id": key_id, "public": public, "publisher": str(endorsement.get("publisher", ""))}
    try:
        sig = base64.b64decode(str(endorsement.get("sig", "")), validate=True)
    except ValueError:
        return None
    if (endorsement.get("key_id") != key_id or endorsement.get("public") != public
            or not ed25519.verify(bytes.fromhex(root.public), ENDORSE_CONTEXT + _canonical(claim), sig)):
        return None
    return claim["publisher"] or root.publisher


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def endorse(root: tuple[str, bytes], key_id: str, public: str, publisher: str) -> dict:
    """A built-in key (root = (key id, secret)) vouching for a newer key: build_pack.py stores it beside the new key
    and puts it in every pack that key signs, so apps that only know the old key still show those packs Official."""
    from . import ed25519
    claim = {"key_id": key_id, "public": public, "publisher": publisher}
    sig = ed25519.sign(root[1], ENDORSE_CONTEXT + _canonical(claim))
    return {**claim, "by": root[0], "sig": base64.b64encode(sig).decode("ascii")}


def _verification(files: dict[str, bytes], meta: dict, trusted: dict) -> tuple[str, "Signer | None", list[str]]:
    """(status, signer, problems) of a pack's files: is it the publisher's own, unchanged since it was signed?
    The signature covers the exact pack.json bytes, and pack.json lists every other file's SHA-256.

    The pack carries its public key, so a signature can always be checked. Who the key belongs to is another
    matter: a built-in key (core/preset_keys.py), or one a built-in key endorsed, makes it Official; one the user
    chose to trust makes it Trusted; any other key only proves the pack is unchanged since its signer signed it."""
    from . import ed25519
    from .preset_keys import KEYS
    problems = []
    listed = meta.get("files")
    if isinstance(listed, dict):
        for name, digest in listed.items():
            if name not in files or hashlib.sha256(files[name]).hexdigest() != digest:
                problems.append(f"{name} differs from the pack as it was built")
        extra = sorted(set(files) - set(listed) - {PACK_MANIFEST, SIGNATURE})
        if extra:
            problems.append(f"files added after the pack was built: {', '.join(extra)}")
    raw = files.get(SIGNATURE)
    if raw is None:
        return ("modified" if problems else "unsigned"), None, problems
    try:
        sig = json.loads(raw.decode("utf-8"))
        key_id, alg = str(sig["key_id"]), str(sig.get("alg", "ed25519"))
        signature = base64.b64decode(sig["sig"], validate=True)
        carried = str(sig.get("public") or "").lower()
        if carried:
            bytes.fromhex(carried)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, AttributeError):
        return "modified", None, problems + ["its signature cannot be read"]
    builtin = KEYS.get(key_id)
    if builtin is not None:
        if carried and carried != builtin.public:
            return "modified", None, problems + [f"it claims key {key_id!r} but carries a different public key"]
        public, publisher, status = builtin.public, builtin.publisher, "official"
        if builtin.status == "revoked":
            return "revoked", Signer(key_id, public, publisher), problems + [
                f"signed with key {key_id!r}, which is no longer trusted"]
    elif carried:
        public = carried
        endorsed = _endorsed(sig.get("endorsement"), key_id, public)
        if endorsed is not None:
            publisher, status = endorsed, "official"
        else:
            publisher = str(sig.get("publisher") or meta.get("publisher") or "unknown publisher")
            status = "signed"
    else:
        return "unknown key", None, problems + [
            f"signed with key {key_id!r}, which this version of the app does not know"]
    if alg != "ed25519" or len(public) != 64 or not ed25519.verify(
            bytes.fromhex(public), SIGN_CONTEXT + files[PACK_MANIFEST], signature):
        return "modified", None, problems + ["its signature does not match its contents"]
    signer = Signer(key_id, public, publisher)
    if not isinstance(listed, dict):
        return "modified", signer, problems + ["the signed manifest lists no files"]
    if problems:
        return "modified", signer, problems
    if status == "signed" and signer.fingerprint in trusted:
        status = "trusted"
    return status, signer, problems


def _pack_candidates(folder: Path) -> list[Path]:
    """Pack files and pack source folders in `folder` (or `folder` itself when it is one)."""
    folder = Path(folder)
    if not folder.exists():
        return []
    if folder.is_file():
        return [folder] if folder.suffix.lower() == PACK_EXT else []
    if (folder / PACK_MANIFEST).is_file():
        return [folder]
    out = sorted(p for p in folder.glob(f"*{PACK_EXT}") if p.is_file())
    out += sorted(p for p in folder.iterdir() if p.is_dir() and (p / PACK_MANIFEST).is_file())
    if (folder / "packs").is_dir():          # the private presets repository's root
        out += _pack_candidates(folder / "packs")
    return out


# ---------------------------------------------------------------------- the user's own presets
class MineStore:
    """The user's own presets: one .atlaspreset file per tool. Every change re-reads the file first, so two windows
    (or two copies of the app) never drop each other's saves."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def path(self, tool_id: str) -> Path:
        return self.folder / f"{tool_id}{EXT}"

    def load(self, tool) -> list[Preset]:
        candidates = [self.path(tool.id)] + [self.path(a) for a in getattr(tool, "aliases", ())]
        for path in candidates:
            if path.exists():
                try:
                    found = read_preset_file(path)
                except PresetFormatError:
                    return []
                for p in found:
                    p.tool = tool.id
                return found
        return []

    def _store(self, tool, presets: list[Preset]) -> None:
        _write_json(self.path(tool.id), preset_file_data(presets))

    def names(self, tool) -> list[str]:
        return [p.name for p in self.load(tool)]

    def unique_name(self, tool, name: str) -> str:
        taken = {n.casefold() for n in self.names(tool)}
        if name.casefold() not in taken:
            return name
        n = 2
        while f"{name} ({n})".casefold() in taken:
            n += 1
        return f"{name} ({n})"

    def put(self, tool, name: str, values: dict, tip: str = "", based_on: str = "") -> Preset:
        """Save `values` as `name` (replacing a preset of that name, which keeps its uid)."""
        presets = self.load(tool)
        snap = snapshot(tool, values)
        now = _now()
        for p in presets:
            if p.name.casefold() == name.casefold():
                p.name, p.values, p.modified, p.tool_version = name, snap, now, tool.version
                p.explicit = tuple(snap)
                if tip:
                    p.tip = tip
                self._store(tool, presets)
                return p
        p = Preset(name=name, tool=tool.id, values=snap, tool_version=tool.version, explicit=tuple(snap), tip=tip,
                   created=now, modified=now, based_on=based_on)
        presets.append(p)
        self._store(tool, presets)
        return p

    def add(self, tool, incoming: Iterable[Preset]) -> list[tuple[str, Preset]]:
        """Add presets (from a file), renaming any whose name is taken and skipping any already here unchanged;
        returns (name in the file, preset as stored) for each one added."""
        presets = self.load(tool)
        uids = {p.uid for p in presets}
        added = []
        for src in incoming:
            taken = {p.name.casefold() for p in presets}
            name = src.name
            if name.casefold() in taken:
                same = next((p for p in presets if p.name.casefold() == name.casefold()), None)
                if same is not None and same.values == src.values:
                    continue                                 # already here, unchanged
                n = 2
                while f"{src.name} ({n})".casefold() in taken:
                    n += 1
                name = f"{src.name} ({n})"
            p = Preset(name=name, tool=tool.id, values=dict(src.values),
                       uid=src.uid if (src.uid and src.uid not in uids and src.uid.startswith(f"{MINE}/")) else "",
                       tool_version=src.tool_version, explicit=src.explicit, tip=src.tip, tags=src.tags,
                       created=src.created or _now(), modified=_now(), based_on=src.based_on, extra=dict(src.extra))
            presets.append(p)
            uids.add(p.uid)
            added.append((src.name, p))
        if added:
            self._store(tool, presets)
        return added

    def remove(self, tool, uid: str) -> bool:
        presets = self.load(tool)
        keep = [p for p in presets if p.uid != uid]
        if len(keep) == len(presets):
            return False
        self._store(tool, keep)
        return True

    def rename(self, tool, uid: str, name: str) -> Preset | None:
        presets = self.load(tool)
        target = next((p for p in presets if p.uid == uid), None)
        if target is None:
            return None
        if name.casefold() != target.name.casefold():
            name = self.unique_name(tool, name)
        target.name, target.modified = name, _now()
        self._store(tool, presets)
        return target

    def adopt_legacy(self, tool, legacy: dict) -> int:
        """Move presets saved by Atlas Toolkit 2.0 and earlier (inside tools/<id>.json) into this store. Run again
        after a crash halfway, nothing is duplicated: a preset already here with the same values is skipped."""
        if not isinstance(legacy, dict) or not legacy:
            return 0
        incoming = [Preset(name=str(name), tool=tool.id, values=dict(vals), tool_version="",
                           extra={"origin": "migrated"})
                    for name, vals in legacy.items() if isinstance(vals, dict)]
        return len(self.add(tool, incoming))


# ---------------------------------------------------------------------- the library
@dataclass
class Group:
    title: str
    source: str                  # MINE or a collection id
    presets: list


def _env_dirs() -> list[Path]:
    value = os.environ.get(ENV_DIRS, "")
    return [Path(v).expanduser() for v in value.split(os.pathsep) if v.strip()]


@dataclass
class ImportReport:
    added: list = field(default_factory=list)        # Presets added to My presets
    renamed: list = field(default_factory=list)      # (old name, new name)
    skipped: int = 0                                 # already there, unchanged
    unknown_tools: list = field(default_factory=list)
    pack: Pack | None = None


# ---------------------------------------------------------------------- installing: look first, then act
# What an install plan does with each thing it found. The first group installs; the rest skip ("older" and
# "included" install with downgrade=True; "same" and "blocked" never do).
INSTALLS = ("install", "update", "replace", "add")
ACTION_LABELS = {
    "install": "install", "update": "update", "replace": "replace", "add": "add to My presets",
    "same": "already installed", "older": "older than the installed one", "included": "already included",
    "blocked": "cannot install",
}


@dataclass
class InstallItem:
    """One thing a file holds (a pack, or a file of presets) and what installing it would do."""
    source: str                                       # "bundle.zip › atlas-complete.atlaspack"
    kind: str                                         # "pack" or "presets"
    action: str = "install"                           # see ACTION_LABELS
    note: str = ""                                    # why, in a few words ("version 1 → 2")
    pack: Pack | None = None
    files: dict = field(default_factory=dict)         # the pack's files, as they will be installed
    presets: list = field(default_factory=list)       # a presets file's presets
    unknown_tools: list = field(default_factory=list)
    warnings: list = field(default_factory=list)      # what someone should hear before it installs
    done: bool = False
    report: ImportReport | None = None

    @property
    def installs(self) -> bool:
        return self.action in INSTALLS

    def describe(self) -> str:
        """One line for a dialog or the command line."""
        if self.kind == "pack":
            pk = self.pack
            who = pk.signer.publisher if pk.signer else ""
            status = {"official": f"Official, from {who}", "trusted": f"from {who}, a publisher you trust",
                      "signed": f"signed by {who}, a publisher you have not trusted"}.get(pk.status,
                                                                                         pk.status_label.lower())
            what = f"{pk.title}, version {pk.pack_version} ({len(pk.presets)} presets; {status})"
        else:
            n = len(self.presets)
            what = f"{n} preset{'s' if n != 1 else ''} from {self.source}"
        line = f"{ACTION_LABELS.get(self.action, self.action).capitalize()}: {what}"
        return line + (f": {self.note}" if self.note else "")


@dataclass
class InstallPlan:
    items: list = field(default_factory=list)
    errors: list = field(default_factory=list)        # (what, why) for files that hold nothing installable

    @property
    def installing(self) -> list:
        return [i for i in self.items if i.installs]

    @property
    def warnings(self) -> list:
        return [f"{i.pack.title if i.pack else i.source}: {w}" for i in self.items if i.installs for w in i.warnings]

    @property
    def untrusted(self) -> list:
        """Signers of packs in this plan whose signature holds but whom the app does not know (one each)."""
        out = {}
        for i in self.items:
            if i.installs and i.pack is not None and i.pack.status == "signed" and i.pack.signer:
                out.setdefault(i.pack.signer.fingerprint, i.pack.signer)
        return list(out.values())

    def summary(self) -> list[str]:
        lines = [i.describe() for i in self.items]
        lines += [f"Cannot read {what}: {why}" for what, why in self.errors]
        return lines

    def report(self) -> ImportReport:
        """Everything that was installed, as one report (the last pack installed in .pack)."""
        report = ImportReport()
        for item in self.items:
            if item.report is None:
                continue
            report.pack = item.report.pack or report.pack
            report.added += item.report.added
            report.renamed += item.report.renamed
            report.skipped += item.report.skipped
            report.unknown_tools = sorted(set(report.unknown_tools) | set(item.report.unknown_tools))
        return report


def _content_id(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8") + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


def _found(path: Path) -> tuple[list[tuple[str, str, dict | bytes]], list[tuple[str, str]]]:
    """What a path holds that can be installed: ([(label, "pack" | "presets", pack files | preset file bytes)],
    [(label, why not)]). A path is an .atlaspack, an .atlaspreset, a pack's folder, a folder of those files, or a
    .zip of any of them (a store download with a readme beside the packs, or a pack's folder zipped by hand)."""
    path = Path(path)
    found, errors = [], []
    if path.is_dir():
        if (path / PACK_MANIFEST).is_file():
            return [(path.name, "pack", _read_folder(path))], []
        for child in sorted(path.iterdir()):
            if child.is_file() and child.suffix.lower() in INSTALLABLE or (child / PACK_MANIFEST).is_file():
                f, e = _found(child)
                found += f
                errors += e
        if not found and not errors:
            errors.append((path.name, "the folder holds no presets or preset packs"))
        return found, errors
    try:
        if path.suffix.lower() == EXT:
            return [(path.name, "presets", path.read_bytes())], []
        entries = _zip_entries(path, path.name, MAX_BUNDLE)
    except (OSError, PresetFormatError) as e:
        return [], [(path.name, str(e).removeprefix(f"{path.name} ").removeprefix(f"{path.name}: "))]
    return _found_in_zip(entries, path.name)


def _found_in_zip(entries: dict[str, bytes], label: str, depth: int = 0):
    found, errors = [], []
    real = {n: d for n, d in entries.items()
            if not n.startswith("__MACOSX/") and PurePosixPath(n).name not in (".DS_Store", "Thumbs.db")}
    roots = sorted(n[: -len(PACK_MANIFEST)] for n in real if PurePosixPath(n).name == PACK_MANIFEST)
    if "" in roots:                                   # the zip is a pack
        try:
            return [(label, "pack", _pack_files(real, label))], []
        except PresetFormatError as e:
            return [], [(label, str(e))]
    for root in roots:                                # pack folders inside a bundle
        if any(root.startswith(r) and root != r for r in roots):
            continue
        sub = {n[len(root):]: d for n, d in real.items() if n.startswith(root)}
        try:
            found.append((f"{label} › {root.rstrip('/')}", "pack", _pack_files(sub, label)))
        except PresetFormatError as e:
            errors.append((f"{label} › {root.rstrip('/')}", str(e)))
    for name in sorted(real):
        if any(name.startswith(r) for r in roots if r):
            continue
        inner = f"{label} › {PurePosixPath(name).name}"
        suffix = PurePosixPath(name).suffix.lower()
        if suffix == EXT:
            found.append((inner, "presets", real[name]))
        elif suffix in (PACK_EXT, ZIP_SUFFIX) and depth == 0:
            try:
                f, e = _found_in_zip(_zip_entries(real[name], inner), inner, depth + 1)
            except PresetFormatError as e:
                f, e = [], [(inner, str(e))]
            found += f
            errors += e
    if not found and not errors:
        errors.append((label, "it holds no presets or preset packs"))
    return found, errors


def holds_presets(path) -> bool:
    """Whether a file is something to load into the presets (a .zip only when it holds a pack or preset files):
    lets a dropped or opened .zip go to the presets without guessing from its name."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (EXT, PACK_EXT):
        return True
    if suffix != ZIP_SUFFIX or not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    return any(PurePosixPath(n).name == PACK_MANIFEST or n.lower().endswith((EXT, PACK_EXT)) for n in names)


class PresetLibrary:
    """Every preset the app can offer: the user's own, installed packs and extra pack folders.

    One preset uid shows once: a collection's tiers repeat the same presets (Complete holds Essentials), so the
    copy from the best pack wins (Official first, then trusted, then the higher tier, then the newer version)."""

    def __init__(self, mine_dir: Path | None = None, packs_dir: Path | None = None, extra_dirs: list | None = None,
                 hidden_path: Path | None = None, trusted_path: Path | None = None):
        from .paths import config_dir, data_dir
        self.mine = MineStore(Path(mine_dir) if mine_dir else config_dir() / "presets" / MINE)
        self.packs_dir = Path(packs_dir) if packs_dir else data_dir() / "packs"
        self.extra_dirs = [Path(d) for d in extra_dirs] if extra_dirs is not None else _env_dirs()
        self.hidden_path = Path(hidden_path) if hidden_path else config_dir() / "presets" / "hidden.json"
        self.trusted_path = (Path(trusted_path) if trusted_path
                             else self.hidden_path.with_name("trusted-publishers.json"))
        self._packs: list[Pack] | None = None
        self.broken: list[tuple[Path, str]] = []
        self._listeners: list = []

    # ------------------------------------------------------------------ change notification
    def subscribe(self, fn: Callable) -> None:
        """Call fn(tool_id or None) after every change (None: anything may have changed). Held weakly for bound
        methods, so a closed page's preset bar simply stops listening."""
        import weakref
        self._listeners.append(weakref.WeakMethod(fn) if hasattr(fn, "__self__") else (lambda f=fn: f))

    def notify(self, tool_id: str | None = None) -> None:
        _MATCH_CACHE.clear()
        alive = []
        for ref in self._listeners:
            fn = ref()
            if fn is None:
                continue
            alive.append(ref)
            try:
                fn(tool_id)
            except RuntimeError:         # a Qt widget already deleted under its Python wrapper
                alive.pop()
        self._listeners = alive

    # ------------------------------------------------------------------ packs
    def reload(self) -> None:
        self._packs = None
        _MATCH_CACHE.clear()

    def packs(self) -> list[Pack]:
        if self._packs is None:
            self._packs, self.broken = [], []
            seen = set()
            trusted = self.trusted()
            for folder in [self.packs_dir] + self.extra_dirs:
                for cand in _pack_candidates(folder):
                    try:
                        pack = read_pack(cand, trusted)
                    except PresetFormatError as e:
                        self.broken.append((cand, str(e)))
                        continue
                    if pack.key in seen:         # the same pack installed and on an extra folder: one copy
                        continue
                    seen.add(pack.key)
                    self._packs.append(pack)
            self._packs.sort(key=lambda p: (p.name.casefold(), -p.tier_rank))
        return self._packs

    def pack_presets(self, tool) -> list[Preset]:
        """Pack presets for `tool` (aliases included), one per uid, in pack order."""
        ids = {tool.id, *getattr(tool, "aliases", ())}
        best: dict[str, Preset] = {}
        order: list[str] = []
        rank = lambda p: (p.pack.rank, p.pack.tier_rank, version_tuple(p.pack.pack_version))  # noqa: E731
        for pack in self.packs():
            for p in pack.presets:
                if p.tool not in ids:
                    continue
                if p.uid not in best:
                    order.append(p.uid)
                    best[p.uid] = p
                elif rank(p) > rank(best[p.uid]):
                    best[p.uid] = p
        return [best[u] for u in order]

    def tiers_of(self, preset: Preset) -> list[str]:
        """The tiers of its collection that hold this preset, lowest first ('Essentials', 'Complete')."""
        packs = [pk for pk in self.packs() if pk.collection == preset.source and any(p.uid == preset.uid for p in pk.presets)]
        return [pk.tier.capitalize() for pk in sorted(packs, key=lambda pk: pk.tier_rank)]

    def installed(self, pack_key: str) -> Pack | None:
        return next((p for p in self.packs() if p.key == pack_key), None)

    def _write_pack(self, pack: Pack, files: dict[str, bytes]) -> Pack:
        """Store a pack as <collection>-<tier>.atlaspack in the packs folder, replacing that collection and tier."""
        self.packs_dir.mkdir(parents=True, exist_ok=True)
        for old in self.packs():
            if old.key == pack.key and old.path and old.path.parent == self.packs_dir:
                old.path.unlink(missing_ok=True)
        dst = self.packs_dir / f"{pack.collection}-{pack.tier}{PACK_EXT}"
        fd, tmp = tempfile.mkstemp(dir=self.packs_dir, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(_zip_bytes(files))
        os.replace(tmp, dst)
        self.reload()
        return read_pack(dst, self.trusted())

    def install_pack(self, path: Path) -> Pack:
        """Install one pack (an .atlaspack, a zip of its folder, or the folder) as it is; returns it installed."""
        found, errors = _found(Path(path))
        packs = [f for f in found if f[1] == "pack"]
        if len(packs) != 1:
            raise PresetFormatError(errors[0][1] if errors and not packs else f"{Path(path).name} is not one pack")
        files = packs[0][2]
        pack = self._write_pack(pack_from_files(files, packs[0][0]), files)
        self.notify(None)
        return pack

    def remove_pack(self, pack: Pack) -> bool:
        if pack.path is None or pack.path.parent != self.packs_dir:
            return False             # packs on an extra folder are not ours to delete
        pack.path.unlink(missing_ok=True)
        self.reload()
        self.notify(None)
        return True

    # ------------------------------------------------------------------ installing
    def plan_install(self, paths, registry=None) -> InstallPlan:
        """What loading these files would do, without changing anything: each pack and presets file they hold, and
        whether it installs, updates, replaces, or is skipped (already installed, older, included in a higher tier
        you have, or made for a newer app). Show it, then pass it to install()."""
        if registry is None:
            from .registry import registry as default_registry
            registry = default_registry()
        plan = InstallPlan()
        trusted = self.trusted()
        seen: dict[str, InstallItem] = {}
        for path in paths:
            found, errors = _found(Path(path))
            plan.errors += errors
            for label, kind, payload in found:
                try:
                    item = (self._plan_pack(label, payload, trusted, registry) if kind == "pack"
                            else self._plan_presets(label, payload, registry))
                except PresetFormatError as e:
                    plan.errors.append((label, str(e)))
                    continue
                if item.pack is not None:            # the same pack twice (a bundle and its loose copy): best one
                    prev = seen.get(item.pack.key)
                    if prev is not None:
                        if _better(item.pack, prev.pack):
                            plan.items[plan.items.index(prev)] = item
                            seen[item.pack.key] = item
                        continue
                    seen[item.pack.key] = item
                plan.items.append(item)
        # a lower tier is included in a higher one installed by this same plan
        for item in plan.items:
            if item.kind == "pack" and item.installs and item.action == "install":
                higher = next((o for o in plan.items if o is not item and o.installs and o.pack is not None
                               and o.pack.collection == item.pack.collection
                               and o.pack.tier_rank > item.pack.tier_rank
                               and {p.uid for p in item.pack.presets} <= {p.uid for p in o.pack.presets}), None)
                if higher is not None:
                    item.action, item.note = "included", f"{higher.pack.title} holds every one of its presets"
        return plan

    def _plan_pack(self, label: str, files: dict, trusted: dict, registry) -> InstallItem:
        pack = pack_from_files(files, label, trusted=trusted)
        item = InstallItem(label, "pack", pack=pack, files=files)
        item.unknown_tools = sorted({p.tool for p in pack.presets if p.tool not in registry.tools})
        if not pack.presets:
            item.action, item.note = "blocked", "it holds no presets"
            return item
        if pack.toolkit_min and older(_toolkit_version(), pack.toolkit_min):
            item.action = "blocked"
            item.note = f"it needs Atlas Toolkit {pack.toolkit_min} or later (this is {_toolkit_version()})"
            return item
        if pack.status in ("modified", "revoked", "unknown key"):
            item.warnings.append(f"{pack.status_label}: {'; '.join(pack.problems) or 'its signature does not hold'}")
        elif pack.status == "unsigned":
            item.warnings.append("it is not signed, so nothing shows who made it or that it is unchanged")
        if item.unknown_tools:
            item.note = (f"{sum(1 for p in pack.presets if p.tool in item.unknown_tools)} presets are for tools this "
                         f"version does not have ({', '.join(item.unknown_tools)}); they wait until you update")
        old = self.installed(pack.key)
        if old is None:
            higher = next((pk for pk in self.packs() if pk.collection == pack.collection
                           and pk.tier_rank > pack.tier_rank and not older(pk.pack_version, pack.pack_version)
                           and {p.uid for p in pack.presets} <= {p.uid for p in pk.presets}), None)
            if higher is not None:
                item.action, item.note = "included", f"{higher.title} (installed) holds every one of its presets"
            return item
        old_files = _installed_files(old)
        if old_files is not None and _content_id(old_files) == _content_id(files):
            item.action, item.note = "same", ""
        elif older(old.pack_version, pack.pack_version):
            item.action, item.note = "update", f"version {old.pack_version} → {pack.pack_version}"
        elif older(pack.pack_version, old.pack_version):
            item.action, item.note = "older", f"version {old.pack_version} is installed"
        else:
            item.action, item.note = "replace", f"another copy of version {pack.pack_version}"
        if item.installs and old.rank > pack.rank:
            item.warnings.append(f"the installed copy is {old.status_label}, this one is {pack.status_label}")
        return item

    def _plan_presets(self, label: str, data: bytes, registry) -> InstallItem:
        try:
            presets = parse_preset_data(json.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, ValueError) as e:
            raise PresetFormatError(f"unreadable: {e}") from None
        item = InstallItem(label, "presets", action="add", presets=presets)
        item.unknown_tools = sorted({p.tool for p in presets if p.tool not in registry.tools})
        if not presets:
            item.action, item.note = "blocked", "it holds no presets"
        elif item.unknown_tools:
            item.note = f"skips tools this version does not have: {', '.join(item.unknown_tools)}"
        return item

    def install(self, plan: InstallPlan, accept_warnings: bool = False, downgrade: bool = False, trust: Iterable = (),
                registry=None) -> InstallPlan:
        """Carry out a plan from plan_install(). Items with warnings install only with accept_warnings (someone saw
        them); downgrade also installs what the plan skips as older or included; `trust` = signers (or their
        fingerprints) to trust from now on, before installing."""
        for s in trust:
            signer = s if isinstance(s, Signer) else next((u for u in plan.untrusted if u.fingerprint == s), None)
            if signer is not None:
                self.trust(signer, notify=False)
        trusted = self.trusted()
        if registry is None:
            from .registry import registry as default_registry
            registry = default_registry()
        for item in plan.items:
            go = item.installs or (downgrade and item.action in ("older", "included"))
            if not go or (item.warnings and not accept_warnings):
                continue
            if item.kind == "pack":
                item.pack = self._write_pack(pack_from_files(item.files, item.source, trusted=trusted), item.files)
                item.report = ImportReport(pack=item.pack)
            else:
                item.report = self._add_presets(item.presets, registry)
            item.done = True
        self.notify(None)
        return plan

    def _add_presets(self, incoming: list, registry) -> ImportReport:
        report = ImportReport()
        by_tool: dict[str, list[Preset]] = {}
        for p in incoming:
            tool = registry.tools.get(p.tool)
            if tool is None:
                report.unknown_tools.append(p.tool)
                continue
            by_tool.setdefault(tool.id, []).append(p)
        for tool_id, items in by_tool.items():
            tool = registry.tools[tool_id]
            added = self.mine.add(tool, items)
            for original, got in added:
                if got.name != original:
                    report.renamed.append((original, got.name))
                report.added.append(got)
            report.skipped += len(items) - len(added)
        report.unknown_tools = sorted(set(report.unknown_tools))
        return report

    def import_file(self, path: Path, registry=None) -> ImportReport:
        """Load one file without asking (the command line asks nothing either): an .atlaspreset adds to My presets,
        a pack installs; a .zip may hold several. PresetFormatError when it holds nothing that loads."""
        plan = self.plan_install([path], registry)
        if not plan.items:
            raise PresetFormatError("; ".join(why for _w, why in plan.errors) or f"{Path(path).name} holds nothing")
        self.install(plan, accept_warnings=True, downgrade=True, registry=registry)
        return plan.report()

    # ------------------------------------------------------------------ publishers the user trusts
    def trusted(self) -> dict:
        """{fingerprint: {"publisher", "public", "key_id", "added"}} of publishers the user chose to trust."""
        try:
            data = json.loads(self.trusted_path.read_text(encoding="utf-8")).get("publishers", {})
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def trust(self, signer: Signer, notify: bool = True) -> None:
        t = self.trusted()
        t[signer.fingerprint] = {"publisher": signer.publisher, "public": signer.public, "key_id": signer.key_id,
                                 "added": _now()}
        _write_json(self.trusted_path, {"publishers": t})
        self.reload()
        if notify:
            self.notify(None)

    def untrust(self, fingerprint: str) -> bool:
        t = self.trusted()
        if t.pop(fingerprint, None) is None:
            return False
        _write_json(self.trusted_path, {"publishers": t})
        self.reload()
        self.notify(None)
        return True

    # ------------------------------------------------------------------ hidden pack presets
    def hidden(self) -> set:
        try:
            return set(json.loads(self.hidden_path.read_text(encoding="utf-8")).get("hidden", []))
        except (OSError, ValueError, AttributeError):
            return set()

    def set_hidden(self, uid: str, hide: bool = True) -> None:
        h = self.hidden()
        (h.add if hide else h.discard)(uid)
        _write_json(self.hidden_path, {"hidden": sorted(h)})
        self.notify(uid.split("/")[1] if uid.count("/") >= 2 else None)

    # ------------------------------------------------------------------ what a tool offers
    def groups(self, tool, include_hidden: bool = False) -> list[Group]:
        """Presets for `tool` in display order: the user's own, then one group per collection."""
        out = []
        mine = self.mine.load(tool)
        if mine:
            out.append(Group(MINE_TITLE, MINE, mine))
        hidden = set() if include_hidden else self.hidden()
        by_collection: dict[str, Group] = {}
        for p in self.pack_presets(tool):
            if p.uid in hidden:
                continue
            g = by_collection.get(p.source)
            if g is None:
                g = by_collection[p.source] = Group(p.pack.name, p.source, [])
                out.append(g)
            g.presets.append(p)
        return out

    def presets(self, tool) -> list[Preset]:
        return [p for g in self.groups(tool) for p in g.presets]

    def get(self, tool, uid: str) -> Preset | None:
        return next((p for p in self.presets(tool) if p.uid == uid), None)

    def find(self, tool, ref: str) -> list[Preset]:
        """Presets `ref` names: a uid, 'source:name' (source = mine or a collection id) or a name."""
        everything = self.presets(tool)
        exact = [p for p in everything if p.uid == ref]
        if exact:
            return exact
        source, sep, name = ref.partition(":")
        if sep:
            hits = [p for p in everything if p.source == source and p.name.casefold() == name.casefold()]
            if hits:
                return hits
        return [p for p in everything if p.name.casefold() == ref.casefold()]

    def match(self, tool, values: dict) -> Preset | None:
        return match(tool, values, self.presets(tool))

    # ------------------------------------------------------------------ the user's own
    def save_mine(self, tool, name: str, values: dict, based_on: str = "") -> Preset:
        p = self.mine.put(tool, name, values, based_on=based_on)
        self.notify(tool.id)
        return p

    def duplicate(self, tool, preset: Preset, name: str | None = None) -> Preset:
        """A pack preset copied into My presets (to edit or export it)."""
        values, _ = apply(tool, preset)
        name = self.mine.unique_name(tool, name or preset.name)
        p = self.mine.put(tool, name, values, tip=preset.tip, based_on=preset.uid)
        self.notify(tool.id)
        return p

    def delete_mine(self, tool, uid: str) -> bool:
        done = self.mine.remove(tool, uid)
        self.notify(tool.id)
        return done

    def rename_mine(self, tool, uid: str, name: str) -> Preset | None:
        p = self.mine.rename(tool, uid, name)
        self.notify(tool.id)
        return p

    def export(self, path: Path, presets: Iterable[Preset]) -> Path:
        """Write the user's own presets to a file. Pack presets cannot be exported (duplicate them first)."""
        presets = list(presets)
        if any(not p.mine for p in presets):
            raise PermissionError("only your own presets can be saved to a file; duplicate a pack preset first")
        return write_preset_file(path, presets)


def _better(a: Pack, b: Pack) -> bool:
    return (a.rank, version_tuple(a.pack_version)) > (b.rank, version_tuple(b.pack_version))


def _installed_files(pack: Pack) -> dict | None:
    try:
        if pack.path is None:
            return None
        return _read_folder(pack.path) if pack.path.is_dir() else _pack_files(_zip_entries(pack.path, pack.path.name),
                                                                             pack.path.name)
    except (OSError, PresetFormatError):
        return None


def pack_bytes(folder: Path, key: tuple[str, bytes] | None = None, publisher: str = "",
               endorsement: dict | None = None) -> bytes:
    """A pack source folder as .atlaspack bytes: entries in name order with fixed timestamps (the same folder always
    gives the same file), every file's SHA-256 in pack.json, and with `key` = (key id, Ed25519 secret) a
    signature.json over the exact pack.json bytes that also carries the public key (and, for a key newer than the
    app's built-in ones, the `endorsement` an older built-in key gave it), so the one file holds everything needed
    to check it. The private presets repository's build tool calls this."""
    from . import ed25519
    folder = Path(folder)
    files = _read_folder(folder)
    files.pop(SIGNATURE, None)
    meta = json.loads(files.pop(PACK_MANIFEST).decode("utf-8"))
    meta["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    files[PACK_MANIFEST] = (json.dumps(meta, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    if key is not None:
        key_id, secret = key
        sig = ed25519.sign(secret, SIGN_CONTEXT + files[PACK_MANIFEST])
        signature = {"key_id": key_id, "alg": "ed25519", "public": ed25519.public_key(secret).hex(),
                     "publisher": publisher or str(meta.get("publisher") or ""),
                     "sig": base64.b64encode(sig).decode("ascii")}
        if endorsement:
            signature["endorsement"] = endorsement
        files[SIGNATURE] = (json.dumps(signature, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    return _zip_bytes(files)
