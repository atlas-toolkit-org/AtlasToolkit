"""
The model catalogue: ready-made 3D models to try the mesh tools with, browsed by category (ui/catalogue.py). No Qt.

The index (catalogue/index.json, written by scripts/catalogue.py from catalogue/recipes.toml) lists categories and
models. A model comes one of two ways:

    bundled     a .ply.gz inside the app (the starter set: the simple solids and a few scans)
    remote      a .ply.gz in the catalogue's GitHub release, downloaded on first use (its SHA-256 checked)

Shapes made in code (geom/shapes.py) are made into files at build time like the scans, so nothing is computed on the
user's computer. Either way it becomes `<folder>/<Category>/<Name>.ply` with a `<Name>.txt` credit beside it, so tools read it like any
file the user owns and write their outputs next to it. The folder is the `catalogue_dir` option, by default
Documents/Atlas Toolkit/Catalogue (core/paths.catalogue_dir). `.catalogue.json` there records what each file is, so a
model whose catalogue entry changed is fetched again.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import paths

INDEX = "index.json"
STATE = ".catalogue.json"
LICENSES = {
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "LicenseRef-stanford-scanrep": "https://graphics.stanford.edu/data/3Dscanrep/",
}
KINDS = ("bundled", "remote")
_lock = threading.Lock()


def root() -> Path:
    """The catalogue's files inside the app: index, thumbnails, the starter models."""
    return paths.resource("catalogue")


@dataclass
class Category:
    id: str
    name: str
    icon: str
    blurb: str = ""


@dataclass
class Model:
    id: str
    name: str
    category: str
    kind: str
    triangles: int = 0
    file: str = ""                 # bundled / remote: the .ply.gz
    sha256: str = ""               # of that .ply.gz
    bytes: int = 0                 # its size
    generator: str = ""            # made by geom/shapes.py at build time (its name), else a scan or a model
    params: dict = field(default_factory=dict)
    description: str = ""
    tags: tuple = ()
    author: str = ""               # the artist or maker of the original
    made: str = ""                 # when
    credit: str = ""               # who scanned or modelled it
    source_url: str = ""
    license: str = ""
    changes: str = ""
    thumbnail: str = ""

    @property
    def license_url(self) -> str:
        return LICENSES.get(self.license, "")

    @property
    def tag(self) -> str:
        """What makes this file this model: a new hash means fetch it again."""
        return self.sha256

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        text = " ".join([self.name, self.description, self.author, self.credit, " ".join(self.tags)]).lower()
        return all(word in text for word in q.split())

    def credit_text(self) -> str:
        lines = [self.name]
        if self.description:
            lines.append(self.description)
        lines.append("")
        if self.author:
            lines.append(f"Original: {self.author}" + (f" ({self.made})" if self.made else ""))
        if self.credit:
            lines.append(f"Scan or model: {self.credit}")
        if self.source_url:
            lines.append(f"Source: {self.source_url}")
        if self.license:
            lines.append(f"Licence: {self.license}" + (f" ({self.license_url})" if self.license_url else ""))
        if self.changes:
            lines.append(f"Changes: {self.changes}")
        lines.append("From the Atlas Toolkit model catalogue.")
        return "\n".join(lines) + "\n"


def safe_name(name: str) -> str:
    """A file name that works on every OS."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return s or "model"


# ----------------------------------------------------------------------------- PLY
def write_ply(path: Path, vertices, faces, comments=()) -> Path:
    """Binary little-endian PLY (float32 positions, int32 triangle indices) with comment lines."""
    V = np.ascontiguousarray(vertices, dtype="<f4")
    F = np.asarray(faces, dtype="<i4")
    head = ["ply", "format binary_little_endian 1.0"]
    head += [f"comment {c}" for c in comments]
    head += [f"element vertex {len(V)}", "property float x", "property float y", "property float z",
             f"element face {len(F)}", "property list uchar int vertex_indices", "end_header"]
    rows = np.empty(len(F), dtype=[("n", "u1"), ("v", "<i4", (3,))])
    rows["n"] = 3
    rows["v"] = F
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with tmp.open("wb") as out:
        out.write(("\n".join(head) + "\n").encode("ascii", "replace"))
        out.write(V.tobytes())
        out.write(rows.tobytes())
    os.replace(tmp, path)
    return path


def ply_comments(model: Model) -> list[str]:
    """The credit, kept inside the file too; and the canonical frame (Z up, front -Y) so tools need not guess."""
    out = [f"atlas-toolkit catalogue: {model.id}", f"name: {model.name}"]
    if model.author:
        out.append(f"original: {model.author}" + (f" ({model.made})" if model.made else ""))
    if model.credit:
        out.append(f"scan or model: {model.credit}")
    if model.source_url:
        out.append(f"source: {model.source_url}")
    if model.license:
        out.append(f"licence: {model.license}")
    out.append('atlas-frame: {"up": "+z", "front": "-y", "rotate": [0.0, 0.0, 0.0]}')
    return [c.replace("\n", " ") for c in out]


# ----------------------------------------------------------------------------- the catalogue
class Catalogue:
    def __init__(self, index: dict, folder: Path | None = None, files: Path | None = None):
        self.data = index
        self.base_url = index.get("base_url", "")
        self.revision = index.get("revision", 1)
        self.files = Path(files) if files else root()
        self.folder = Path(folder) if folder else paths.catalogue_dir()
        self.categories = [Category(**c) for c in index.get("categories", [])]
        self._models = [_model(m) for m in index.get("models", [])]
        self._by_id = {m.id: m for m in self._models}

    @classmethod
    def load(cls, folder: Path | None = None, settings=None, files: Path | None = None) -> "Catalogue":
        files = Path(files) if files else root()
        index = json.loads((files / INDEX).read_text(encoding="utf-8"))
        if folder is None and settings is not None:
            chosen = str(settings.get("catalogue_dir", "") or "").strip()
            folder = Path(chosen) if chosen else None
        return cls(index, folder, files)

    # --- browsing
    def models(self, category: str | None = None, query: str = "") -> list[Model]:
        return [m for m in self._models if (category is None or m.category == category) and m.matches(query)]

    def get(self, model_id: str) -> Model:
        return self._by_id[model_id]

    def category(self, cat_id: str) -> Category:
        return next(c for c in self.categories if c.id == cat_id)

    def thumbnail(self, model: Model) -> Path:
        return self.files / "thumbs" / (model.thumbnail or f"{model.id}.png")

    def bundled_file(self, model: Model) -> Path:
        return self.files / "models" / model.file

    # --- where things go
    def path_for(self, model: Model) -> Path:
        cat = next((c.name for c in self.categories if c.id == model.category), model.category)
        return self.folder / safe_name(cat) / f"{safe_name(model.name)}.ply"

    def _state(self) -> dict:
        try:
            return json.loads((self.folder / STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / (STATE + ".part")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.folder / STATE)

    def is_local(self, model: Model) -> bool:
        """Its file is in the folder and is this entry's (not an older one's)."""
        entry = self._state().get(model.id)
        return bool(entry and entry.get("tag") == model.tag and self.path_for(model).is_file())

    def status(self, model: Model) -> str:
        """local | bundled | remote: what using it will take."""
        if self.is_local(model):
            return "local"
        if model.kind == "bundled" and not self.bundled_file(model).is_file():
            return "remote"
        return model.kind

    def needs_download(self, model: Model) -> bool:
        return self.status(model) == "remote"

    def url(self, model: Model) -> str:
        return self.base_url.rstrip("/") + "/" + model.file

    # --- getting a model
    def ensure(self, model: Model, progress=None, cancelled=None, opener=None) -> Path:
        """The model's file in the folder, downloaded or unpacked first when needed. progress(stage, fraction) with
        stage downloading | unpacking; cancelled() → True stops a download (DownloadError)."""
        if self.is_local(model):
            return self.path_for(model)
        dest = self.path_for(model)
        dest.parent.mkdir(parents=True, exist_ok=True)
        status = self.status(model)
        if status == "remote":
            from .download import fetch
            cache = paths.cache_dir() / "catalogue"
            packed = cache / model.file
            if not _verified(packed, model.sha256):
                fetch(self.url(model), packed, model.sha256, model.bytes,
                      (lambda done, total: progress("downloading", done / total if total else None))
                      if progress else None, cancelled, opener=opener)
        else:
            packed = self.bundled_file(model)
            if not _verified(packed, model.sha256):
                raise ValueError(f"The bundled model {model.file} is damaged. Reinstall Atlas Toolkit.")
        if progress:
            progress("unpacking", None)
        tmp = dest.with_name(dest.name + ".part")
        with gzip.open(packed, "rb") as src, tmp.open("wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        os.replace(tmp, dest)
        if status == "remote":
            packed.unlink(missing_ok=True)
        dest.with_suffix(".txt").write_text(model.credit_text(), encoding="utf-8")
        with _lock:
            state = self._state()
            state[model.id] = {"tag": model.tag, "file": str(dest.relative_to(self.folder))}
            self._save_state(state)
        return dest

    # --- tidying
    def local_models(self) -> list[Model]:
        return [m for m in self._models if self.is_local(m)]

    def remove(self, model: Model) -> None:
        p = self.path_for(model)
        p.unlink(missing_ok=True)
        p.with_suffix(".txt").unlink(missing_ok=True)
        with _lock:
            state = self._state()
            state.pop(model.id, None)
            if self.folder.is_dir():
                self._save_state(state)
        try:
            p.parent.rmdir()                     # only when nothing else (an output) is in it
        except OSError:
            pass

    def remove_all(self) -> int:
        """Every catalogue model in the folder (outputs written next to them stay). Returns how many."""
        local = self.local_models()
        for m in local:
            self.remove(m)
        return len(local)

    def disk_usage(self) -> int:
        """Bytes the catalogue's own files take in the folder (not the outputs beside them)."""
        total = 0
        for m in self.local_models():
            for p in (self.path_for(m), self.path_for(m).with_suffix(".txt")):
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total


def _model(d: dict) -> Model:
    d = dict(d)
    d["tags"] = tuple(d.get("tags", ()))
    known = Model.__dataclass_fields__
    return Model(**{k: v for k, v in d.items() if k in known})


def _verified(path: Path, sha256: str) -> bool:
    """The file is there and is the expected one (hashed once per size and time, remembered beside it)."""
    if not path.is_file():
        return False
    marker = path.with_name(path.name + ".verified")
    st = path.stat()
    stamp = f"{sha256} {st.st_size} {st.st_mtime_ns}"
    try:
        if marker.read_text(encoding="ascii").strip() == stamp:
            return True
    except OSError:
        pass
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    if h.hexdigest() != sha256:
        return False
    try:
        marker.write_text(stamp, encoding="ascii")
    except OSError:                                  # read-only (inside the app): hash again next time
        pass
    return True


# ----------------------------------------------------------------------------- command line
def add_parser(sub) -> None:
    c = sub.add_parser("catalogue", help="the model catalogue: list its models, get one, remove what was fetched",
                       description="Ready-made models to try the tools with, as in the app's Model catalogue window. "
                                   "'get' prints the file's path, so it can feed 'run'.")
    cs = c.add_subparsers(dest="action")
    cl = cs.add_parser("list", help="every model: id, category, triangles, and whether it is on this computer")
    cl.add_argument("category", nargs="?", help="only this category (its id)")
    cg = cs.add_parser("get", help="fetch, unpack or build models; prints each file's path")
    cg.add_argument("ids", nargs="+")
    cr = cs.add_parser("remove", help="delete fetched models from the catalogue folder (outputs beside them stay)")
    cr.add_argument("ids", nargs="*", help="the models (default: all of them)")


def command(args, settings=None) -> int:
    import sys
    if settings is None:
        from .settings import AppSettings
        settings = AppSettings()
    c = Catalogue.load(settings=settings)
    action = args.action or "list"
    if action == "list":
        cats = {k.id: k.name for k in c.categories}
        if args.category and args.category not in cats:
            print(f"no category {args.category!r}: {', '.join(cats)}", file=sys.stderr)
            return 2
        for m in c.models(args.category):
            tris = f"{m.triangles:>9,}".replace(",", " ")
            print(f"{m.id:28s} {m.category:9s} {tris} △  {c.status(m):10s} {m.name}")
        print(f"\nFolder: {c.folder}", file=sys.stderr)
        return 0
    try:
        chosen = [c.get(i) for i in args.ids] if args.ids else (c.local_models() if action == "remove" else [])
    except KeyError as e:
        print(f"no model {e.args[0]!r}: 'catalogue list' shows the ids", file=sys.stderr)
        return 2
    if action == "remove":
        for m in chosen:
            c.remove(m)
        print(f"Removed {len(chosen)} model(s) from {c.folder}", file=sys.stderr)
        return 0
    code = 0
    for m in chosen:
        try:
            def say(stage, frac, name=m.name):
                print(f"\r{stage} {name}" + (f" {frac * 100:3.0f}%" if frac is not None else ""), end="",
                      file=sys.stderr, flush=True)
            path = c.ensure(m, progress=say)
            print("", file=sys.stderr)
            print(path)
        except Exception as e:                               # network, hash: say it and go on with the rest
            print(f"\n{m.id}: {e}", file=sys.stderr)
            code = 1
    return code
