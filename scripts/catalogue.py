"""
Build the model catalogue (core/catalogue.py) from catalogue/recipes.toml. Network access is build-time only.

    python scripts/catalogue.py all                 fetch, prepare, thumbs, index, bundle, check
    python scripts/catalogue.py fetch [--only id,id]   sources into build/catalogue/sources (pinned in sources.lock.json)
    python scripts/catalogue.py prepare             orient, crop, clean, decimate, scale → build/catalogue/out/<id>.ply.gz
    python scripts/catalogue.py thumbs              the cards' pictures → src/atlas_toolkit/catalogue/thumbs/<id>.png
    python scripts/catalogue.py index               index.json and licenses/catalogue-models.txt
    python scripts/catalogue.py bundle              the starter set into the package (from out/, else the release)
    python scripts/catalogue.py check               licences, sizes, hashes, thumbnails, ids
    python scripts/catalogue.py sheet               a contact sheet of every card for review (build/qa/catalogue)
    python scripts/catalogue.py publish             upload out/*.ply.gz to the catalogue release (only when asked)

Adding a model is one [[model]] block in catalogue/recipes.toml and `all --only <id>`; see docs/CATALOGUE.md.
Commons sources (`commons = "File name.stl"`) are checked against Wikimedia Commons' own licence field: a recipe whose
licence differs from what Commons says fails. Other sources name `url` and quote the licence in `license_evidence`.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atlas_toolkit.core import catalogue as cat  # noqa: E402

RECIPES = ROOT / "catalogue" / "recipes.toml"
LOCK = ROOT / "catalogue" / "sources.lock.json"
WORK = ROOT / "build" / "catalogue"
SOURCES = WORK / "sources"
OUT = WORK / "out"
PKG = ROOT / "src" / "atlas_toolkit" / "catalogue"
NOTICE = ROOT / "src" / "atlas_toolkit" / "licenses" / "catalogue-models.txt"
# Wikimedia asks scripts to say who they are, with a contact link (meta.wikimedia.org/wiki/User-Agent_policy)
AGENT = "AtlasToolkit-catalogue-build/1.0 (https://github.com/atlas-toolkit-org/AtlasToolkit)"
# Commons' licence names, as its API gives them, and ours
COMMONS_LICENSES = {"CC0": "CC0-1.0", "CC BY 4.0": "CC-BY-4.0", "CC BY-SA 4.0": "CC-BY-SA-4.0",
                     "Public domain": "LicenseRef-public-domain"}
ALLOWED = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "LicenseRef-public-domain"}
LIMITS = {"triangles": (8_000, 1_600_000), "bytes": 20_000_000,
          "thin": 0.4}        # at most this share of the surface may be thinner than 3% of the model's size
THUMB = 384
# generated shapes: their "source" is the code that makes them
SHAPES_SOURCE = "https://github.com/atlas-toolkit-org/AtlasToolkit/blob/main/src/atlas_toolkit/geom/shapes.py"


# ----------------------------------------------------------------------------- recipes
# what a `from = "<id>"` recipe takes from the model it lightens (its own keys win)
INHERITED = ("commons", "url", "member", "source_url", "license", "license_evidence", "author", "made", "credit", "up",
             "front", "rotate", "upright", "crop", "keep", "subdivide", "generator", "params", "view", "changes",
             "description")


def recipes() -> dict:
    data = tomllib.loads(RECIPES.read_text(encoding="utf-8"))
    ids = [m["id"] for m in data["model"]]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"duplicate ids in recipes.toml: {sorted(dup)}")
    by_id = {m["id"]: m for m in data["model"]}
    for i, m in enumerate(data["model"]):
        if "from" in m:                              # a lighter version of another model: same source, same turns
            base = by_id.get(m["from"])
            if base is None or "from" in base:
                raise SystemExit(f"{m['id']}: from = {m['from']!r} must name a model that is not itself a copy")
            merged = {k: base[k] for k in INHERITED if k in base}
            merged.update(m)
            merged["tags"] = list(dict.fromkeys([*base.get("tags", []), *m.get("tags", [])]))
            light = f"a lighter version (at most {int(m.get('triangles', 0)):,} triangles)".replace(",", " ")
            merged["changes"] = f"{base['changes']}; {light}" if base.get("changes") else light
            data["model"][i] = merged
    return data


def chosen(data, only) -> list[dict]:
    if not only:
        return data["model"]
    want = set(only.split(","))
    missing = want - {m["id"] for m in data["model"]}
    if missing:
        raise SystemExit(f"no such models: {sorted(missing)}")
    return [m for m in data["model"] if m["id"] in want]


def lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.is_file() else {}


def save_lock(d: dict) -> None:
    LOCK.write_text(json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def http(url: str, dest: Path | None = None, tries: int = 6):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=120) as r:
                if dest is None:
                    return r.read()
                tmp = dest.with_name(dest.name + ".part")
                with tmp.open("wb") as out:
                    shutil.copyfileobj(r, out, 1 << 20)
                tmp.replace(dest)
                return dest
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            wait = int(e.headers.get("Retry-After", 0) or 0) if e.headers else 0
            time.sleep(max(wait, 30 * (attempt + 1)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
            time.sleep(10)


def commons_info(title: str) -> dict:
    q = urllib.parse.urlencode(dict(action="query", titles="File:" + title, prop="imageinfo",
                                    iiprop="url|size|sha1|extmetadata", format="json"))
    d = json.loads(http("https://commons.wikimedia.org/w/api.php?" + q))
    page = next(iter(d["query"]["pages"].values()))
    if "imageinfo" not in page:
        raise SystemExit(f"Commons has no file named {title!r}")
    ii = page["imageinfo"][0]
    em = ii.get("extmetadata", {})
    return {"url": ii["url"].split("?")[0], "page": ii["descriptionurl"], "sha1": ii["sha1"], "bytes": ii["size"],
            "license": (em.get("LicenseShortName") or {}).get("value", "")}


# ----------------------------------------------------------------------------- fetch
def fetch(data, only) -> None:
    pins = lock()
    for m in chosen(data, only):
        if m.get("generator") or m.get("from"):            # a copy uses the source of the model it lightens
            continue
        folder = SOURCES / m["id"]
        folder.mkdir(parents=True, exist_ok=True)
        pin = pins.get(m["id"], {})
        if m.get("commons"):
            info = commons_info(m["commons"])
            ours = COMMONS_LICENSES.get(info["license"])
            if ours != m["license"]:
                raise SystemExit(f"{m['id']}: Commons says {info['license']!r}, the recipe says {m['license']!r}")
            url, source_page = info["url"], info["page"]
        else:
            if not m.get("license_evidence"):
                raise SystemExit(f"{m['id']}: a non-Commons source needs license_evidence (a quote of its terms)")
            url, source_page = m["url"], m["source_url"]
        name = urllib.parse.unquote(url.rsplit("/", 1)[1])
        dest = folder / name
        if not (dest.is_file() and pin.get("sha256") == sha256(dest)):
            print(f"  {m['id']}: downloading {url}")
            http(url, dest)
            if "wikimedia.org" in url:
                time.sleep(3)                       # a polite pace between Commons files
        digest = sha256(dest)
        if pin.get("sha256") and pin["sha256"] != digest:
            raise SystemExit(f"{m['id']}: the source changed (SHA-256 {digest} is not the pinned {pin['sha256']}). "
                             "Check it, then delete its entry in sources.lock.json to pin the new one.")
        pins[m["id"]] = {"url": url, "page": source_page, "file": name, "sha256": digest,
                         "bytes": dest.stat().st_size}
        save_lock(pins)
        print(f"  {m['id']}: {name} ({dest.stat().st_size / 1e6:.1f} MB) ok")


def source_mesh_path(m) -> Path:
    src = m.get("from", m["id"])
    pin = lock().get(src)
    if not pin:
        raise SystemExit(f"{src}: not fetched yet")
    path = SOURCES / src / pin["file"]
    if path.suffix.lower() == ".zip":
        member = m.get("member", "*.obj")
        unpacked = SOURCES / src / "unpacked"
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if fnmatch(n.lower(), member.lower()) and not n.startswith("__MACOSX")]
            if not names:
                raise SystemExit(f"{m['id']}: nothing in {path.name} matches {member!r}: {z.namelist()[:20]}")
            target = unpacked / Path(names[0]).name
            if not target.is_file():
                unpacked.mkdir(exist_ok=True)
                with z.open(names[0]) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
        return target
    return path


# ----------------------------------------------------------------------------- prepare
def _mesh_for(m):
    """The model as it will ship: (vertices, faces) in the canonical frame, before writing."""
    import numpy as np
    import trimesh

    from atlas_toolkit.geom import frame3d, shapes
    from atlas_toolkit.geom.mesh import load_mesh
    if m.get("generator"):
        mesh = shapes.build(m["generator"], **m.get("params", {}))
        return _repaired(m, np.asarray(mesh.vertices), np.asarray(mesh.faces))
    path = source_mesh_path(m)
    mesh = load_mesh(path)
    if not isinstance(mesh, trimesh.Trimesh):
        mesh = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    up, front = m.get("up"), m.get("front")
    if not (up and front):
        gu, gf = frame3d.guess_axes(mesh.extents, frame3d.fmt_of(path))
        up, front = up or gu, front or gf
    R = frame3d.rotation(up, front, (0.0, 0.0, 0.0))
    mesh.vertices = mesh.vertices @ R.T
    if m.get("upright") == "base":                       # scans stored at a slant: stand them on their plinth
        mesh.vertices = mesh.vertices @ _base_rotation(mesh).T
    E = frame3d.euler_xyz(tuple(m.get("rotate", (0.0, 0.0, 0.0))))
    mesh.vertices = mesh.vertices @ E.T
    for axis, lo_frac, hi_frac in _crops(m):
        lo, hi = mesh.bounds
        k = "xyz".index(axis)
        n = np.zeros(3)
        if lo_frac is not None:
            n[k] = 1.0
            mesh = mesh.slice_plane(lo + (hi - lo) * np.eye(3)[k] * lo_frac * 1.0, n, cap=True)
        lo2, hi2 = lo, hi
        if hi_frac is not None:
            n = np.zeros(3)
            n[k] = -1.0
            mesh = mesh.slice_plane(lo2 + (hi2 - lo2) * np.eye(3)[k] * hi_frac, n, cap=True)
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        keep = m.get("keep", "pieces")
        if keep == "largest":
            mesh = max(parts, key=lambda p: len(p.faces))
        else:                                             # drop scan crumbs, keep real pieces
            big = max(len(p.faces) for p in parts)
            mesh = trimesh.util.concatenate([p for p in parts if len(p.faces) >= 0.002 * big])
    target = int(m.get("triangles", 600_000))
    if len(mesh.faces) > target:
        mesh = mesh.simplify_quadric_decimation(face_count=target)
    elif m.get("subdivide") and len(mesh.faces) < target:
        while len(mesh.faces) * 4 <= target * 1.2:
            v, f = trimesh.remesh.subdivide_loop(mesh.vertices, mesh.faces, iterations=1) if m["subdivide"] == "smooth" \
                else trimesh.remesh.subdivide(mesh.vertices, mesh.faces)
            mesh = trimesh.Trimesh(v, f, process=False)
    mesh.remove_unreferenced_vertices()
    V, F = _repaired(m, np.asarray(mesh.vertices), np.asarray(mesh.faces))
    mesh = trimesh.Trimesh(V, F, process=False)
    shapes.normalise(mesh, float(m.get("size", shapes.SIZE * 2)))
    return np.asarray(mesh.vertices), np.asarray(mesh.faces)


def _repaired(m, V, F):
    """Every model ships as a clean closed solid, so every tool takes it as it is: decimation leaves back-to-back
    faces and crumbs, scans leave holes and folds (geom/repair.py, never resampled here: detail is kept). A model
    that cannot be one (`open = "<why>"` in its recipe: a non-orientable surface) is left as it is."""
    import numpy as np

    from atlas_toolkit.geom import repair
    if m.get("open"):
        return V, F
    d = repair.diagnose_arrays(np.asarray(V, float), np.asarray(F, np.int64))
    if d.solid and d.manifold:          # a clean closed solid, and no parts touching at a point (bow-ties)
        return V, F
    V, F, notes, _ = repair.repair_arrays(V, F, repair.Settings(strategy="repair"))
    for note in notes:
        print(f"    {m['id']}: {note}")
    return V, F


def _base_rotation(mesh):
    """The turn that stands a slanted scan on its plinth: the largest flat region whose normal points away from the
    model's mass (a plinth's underside, not its top) is made to face straight down."""
    import numpy as np
    n = np.asarray(mesh.face_normals)
    a = np.asarray(mesh.area_faces)
    centres = np.asarray(mesh.triangles_center)
    theta = np.degrees(np.arccos(np.clip(n[:, 2], -1, 1)))
    phi = np.degrees(np.arctan2(n[:, 1], n[:, 0]))
    key = np.round(theta / 1.5).astype(np.int64) * 1000 + np.round(phi / 3).astype(np.int64)
    _keys, inv = np.unique(key, return_inverse=True)
    area = np.bincount(inv, weights=a)
    mass = np.asarray(mesh.vertices).mean(0)
    size = float(mesh.extents.max())
    best, best_area = None, 0.0
    for b in np.argsort(area)[::-1][:12]:
        sel = inv == b
        v = (n[sel] * a[sel, None]).sum(0)
        v /= np.linalg.norm(v)
        p = (centres[sel] * a[sel, None]).sum(0) / a[sel].sum()
        if (mass - p) @ v < -0.15 * size and area[b] > best_area:    # the model lies behind it: an underside
            best, best_area = v, area[b]
    if best is None:
        return np.eye(3)
    target = np.array([0.0, 0.0, -1.0])
    axis = np.cross(best, target)
    s, c = np.linalg.norm(axis), float(best @ target)
    if s < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    k = axis / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + s * K + (1 - c) * (K @ K)


def _crops(m):
    """crop = {z = [0.86, 1.0], x = [0.1, 0.9]}: keep that share of the canonical bounding box along each axis."""
    for axis, (lo, hi) in (m.get("crop") or {}).items():
        yield axis, (lo if lo > 0 else None), (hi if hi < 1 else None)


def prepare(data, only) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for m in chosen(data, only):
        t = time.time()
        V, F = _mesh_for(m)
        model = _model_of(m, data)
        ply = OUT / f"{m['id']}.ply"
        cat.write_ply(ply, V, F, cat.ply_comments(model))
        gz = OUT / f"{m['id']}.ply.gz"
        with ply.open("rb") as src, gzip.GzipFile(gz, "wb", compresslevel=9, mtime=0) as out:
            shutil.copyfileobj(src, out, 1 << 20)
        ply.unlink()
        stats = {"triangles": int(len(F)), "vertices": int(len(V)), "sha256": sha256(gz), "bytes": gz.stat().st_size,
                 "thin": thinness(V, F), "solid": solid_state(V, F)}
        (OUT / f"{m['id']}.json").write_text(json.dumps(stats), encoding="utf-8")
        print(f"  {m['id']}: {len(F):,} triangles, {stats['bytes'] / 1e6:.1f} MB ({time.time() - t:.0f}s)")


def solid_state(V, F) -> str:
    """"" for a clean closed solid, else what is wrong with it (geom/repair.py's diagnosis)."""
    import numpy as np

    from atlas_toolkit.geom import repair
    d = repair.diagnose_arrays(np.asarray(V, float), np.asarray(F, np.int64))
    return "" if d.solid and d.manifold else d.summary()


def thinness(V, F, samples: int = 3000) -> float:
    """The share of the surface thinner than 3% of the model's largest side: rays cast inward from even samples
    (wires, bones, legs, the walls of hollow vessels score high; busts and solids near zero)."""
    import numpy as np
    import trimesh

    from atlas_toolkit.geom.raycast import Caster
    mesh = trimesh.Trimesh(V, F, process=False)
    size = float(mesh.extents.max())
    pts, fi = trimesh.sample.sample_surface_even(mesh, samples, seed=1)
    n = mesh.face_normals[fi]
    c = Caster(mesh.vertices, mesh.faces)
    t, *_ = c.intersect(pts - n * size * 1e-4, -n)
    t2, *_ = c.intersect(pts + n * size * 1e-4, n)       # a surface facing in: measure the other way
    th = np.where(np.isfinite(t), t, t2)
    th = np.where(np.isfinite(th), th, size)
    return round(float((th < 0.03 * size).mean()), 3)


def _stats(m) -> dict:
    f = OUT / f"{m['id']}.json"
    if not f.is_file():
        raise SystemExit(f"{m['id']}: not prepared yet")
    return json.loads(f.read_text())


def _model_of(m, data) -> cat.Model:
    pin = lock().get(m.get("from", m["id"]), {})
    kind = "bundled" if m.get("bundled") else "remote"
    made = bool(m.get("generator"))
    return cat.Model(
        id=m["id"], name=m["name"], category=m["category"], kind=kind, file=f"{m['id']}.ply.gz",
        generator=m.get("generator", ""), params=m.get("params", {}),
        description=m.get("description", ""), tags=tuple(m.get("tags", ())), author=m.get("author", ""),
        made=m.get("made", ""), credit=m.get("credit", "Atlas Toolkit (geom/shapes.py)" if made else ""),
        source_url=m.get("source_url") or pin.get("page", "") or (SHAPES_SOURCE if made else ""),
        license=m.get("license", "CC0-1.0" if made else ""),
        changes=m.get("changes", ""), thumbnail=f"{m['id']}.png")


# ----------------------------------------------------------------------------- thumbs
def thumbs(data, only, force=False) -> None:
    from atlas_toolkit.geom.thumbnail import render
    (PKG / "thumbs").mkdir(parents=True, exist_ok=True)
    for m in chosen(data, only):
        dest = PKG / "thumbs" / f"{m['id']}.png"
        stamp = OUT / f"{m['id']}.thumb"
        tag = _thumb_tag(m)
        if dest.is_file() and stamp.is_file() and stamp.read_text() == tag and not force:
            continue
        t = time.time()
        V, F = _read_out(m["id"])
        view = m.get("view", {})
        img = render(V, F, THUMB, azimuth=float(view.get("azimuth", -28)), elevation=float(view.get("elevation", 14)))
        save_thumb(img, dest)
        stamp.write_text(tag)
        print(f"  {m['id']}: thumbnail ({time.time() - t:.0f}s)")


def save_thumb(img, dest: Path) -> None:
    """Grey with alpha (the clay is all but neutral): every shade kept, no banding on smooth forms, a third the size."""
    img.convert("LA").save(dest, optimize=True)


def _thumb_tag(m) -> str:
    st = OUT / f"{m['id']}.json"
    base = json.dumps({k: m.get(k) for k in ("generator", "params", "view")}, sort_keys=True)
    return base + (st.read_text() if st.is_file() else "")


def _read_out(mid: str):
    import io

    from trimesh.exchange.ply import load_ply
    with gzip.open(OUT / f"{mid}.ply.gz", "rb") as f:
        d = load_ply(io.BytesIO(f.read()))
    return d["vertices"], d["faces"]


# ----------------------------------------------------------------------------- index and notices
def index(data, partial: bool = False) -> None:
    c = data["catalogue"]
    out = {"schema": 1, "revision": c["revision"], "base_url": c["base_url"], "categories": data["category"],
           "models": []}
    for m in data["model"]:
        if partial and not (OUT / f"{m['id']}.json").is_file():
            print(f"  {m['id']}: not prepared, left out (--partial)")
            continue
        model = _model_of(m, data)
        st = _stats(m)
        d = {k: v for k, v in model.__dict__.items() if v not in ("", (), {}, None)}
        d["tags"] = list(model.tags)
        d["triangles"] = st.get("triangles", 0)
        d["sha256"], d["bytes"] = st["sha256"], st["bytes"]
        out["models"].append(d)
    (PKG / cat.INDEX).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    notice(data)
    print(f"  index: {len(out['models'])} models in {len(out['categories'])} categories")


def notice(data) -> None:
    lines = ["Atlas Toolkit model catalogue: credits and licences", "=" * 52, "",
             "The model catalogue (Model catalogue… beside a mesh tool's inputs) offers the models below. Scanned and",
             "modelled works are the property of their authors and are redistributed under the licences named; each",
             "was decimated, re-oriented (Z up, front toward -Y), cleaned (holes closed, stray faces removed), centred",
             "and scaled, and saved as binary PLY, and some were cropped (see Changes). Generated shapes are built by",
             "the app (geom/shapes.py) and dedicated to the public domain (CC0 1.0). Licence texts: CC0-1.0.txt,",
             "CC-BY-4.0.txt, CC-BY-SA-4.0.txt in this folder.",
             "Public domain: the source marks the work as free of copyright.", ""]
    names = {c["id"]: c["name"] for c in data["category"]}
    for c in data["category"]:
        ms = [m for m in data["model"] if m["category"] == c["id"] and not m.get("generator")]
        if not ms:
            continue
        lines += [names[c["id"]], "-" * len(names[c["id"]])]
        for m in ms:
            model = _model_of(m, data)
            lines.append(f"* {model.name}")
            if model.author:
                lines.append(f"    Original: {model.author}" + (f" ({model.made})" if model.made else ""))
            if model.credit:
                lines.append(f"    Scan or model: {model.credit}")
            lines.append(f"    Source: {model.source_url}")
            lines.append(f"    Licence: {model.license}" + (f" ({model.license_url})" if model.license_url else ""))
            changes = ("decimated, re-oriented, cleaned (holes closed, stray faces removed), scaled"
                       + (f"; {model.changes}" if model.changes else ""))
            lines.append(f"    Changes: {changes}")
        lines.append("")
    gen = [m["name"] for m in data["model"] if m.get("generator")]
    lines += ["Generated shapes (CC0-1.0, made by Atlas Toolkit)", "-" * 50, ", ".join(gen), ""]
    NOTICE.write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------------- bundle
def bundle(data) -> None:
    """The starter set into the package: from build/catalogue/out when it is there, else from the release."""
    idx = json.loads((PKG / cat.INDEX).read_text(encoding="utf-8"))
    dest = PKG / "models"
    dest.mkdir(exist_ok=True)
    wanted = {m["file"]: m for m in idx["models"] if m["kind"] == "bundled"}
    for f in dest.glob("*.ply.gz"):
        if f.name not in wanted:
            f.unlink()
    for name, m in wanted.items():
        target = dest / name
        if target.is_file() and sha256(target) == m["sha256"]:
            continue
        local = OUT / name
        if local.is_file() and sha256(local) == m["sha256"]:
            shutil.copy2(local, target)
        else:
            print(f"  {m['id']}: downloading the starter model from the release")
            http(idx["base_url"].rstrip("/") + "/" + name, target)
            if sha256(target) != m["sha256"]:
                target.unlink()
                raise SystemExit(f"{m['id']}: the release's {name} is not the indexed one")
    total = sum(m["bytes"] for m in wanted.values())
    print(f"  bundle: {len(wanted)} starter models, {total / 1e6:.1f} MB")


# ----------------------------------------------------------------------------- check
def check(data) -> list[str]:
    problems = []
    idx = json.loads((PKG / cat.INDEX).read_text(encoding="utf-8"))
    cats = {c["id"] for c in idx["categories"]}
    seen = set()
    for m in idx["models"]:
        mid = m["id"]
        if mid in seen:
            problems.append(f"{mid}: duplicate id")
        seen.add(mid)
        if m["category"] not in cats:
            problems.append(f"{mid}: unknown category {m['category']}")
        if m.get("license") not in ALLOWED:
            problems.append(f"{mid}: licence {m.get('license')!r} is not allowed ({sorted(ALLOWED)})")
        if not (PKG / "thumbs" / f"{mid}.png").is_file():
            problems.append(f"{mid}: no thumbnail")
        lo, hi = LIMITS["triangles"]
        if not lo <= m.get("triangles", 0) <= hi:
            problems.append(f"{mid}: {m.get('triangles')} triangles is outside {lo:,}-{hi:,}")
        if m.get("bytes", 0) > LIMITS["bytes"]:
            problems.append(f"{mid}: {m['bytes'] / 1e6:.1f} MB is over {LIMITS['bytes'] / 1e6:.0f} MB")
        if not m.get("source_url"):
            problems.append(f"{mid}: no source page")
        out = OUT / m["file"]
        if out.is_file() and sha256(out) != m["sha256"]:
            problems.append(f"{mid}: build/catalogue/out/{m['file']} differs from the index (run index)")
        if m["kind"] == "bundled":
            f = PKG / "models" / m["file"]
            if f.is_file() and sha256(f) != m["sha256"]:
                problems.append(f"{mid}: the bundled file differs from the index (run bundle)")
    for m in idx["models"]:
        f = OUT / f"{m['id']}.json"
        if not f.is_file():
            continue
        st = json.loads(f.read_text())
        if "thin" not in st and (OUT / m["file"]).is_file():
            st["thin"] = thinness(*_read_out(m["id"]))
            f.write_text(json.dumps(st))
        if "solid" not in st and (OUT / m["file"]).is_file():
            st["solid"] = solid_state(*_read_out(m["id"]))
            f.write_text(json.dumps(st))
        recipe = next((r for r in data["model"] if r["id"] == m["id"]), {})
        if st.get("solid") and not recipe.get("open"):
            problems.append(f"{m['id']}: not a clean closed solid ({st['solid']}): the tools that need one would stop. "
                            "Fix the recipe (crop, keep), or set open = \"<why>\" if it cannot be one")
        if st.get("thin", 0) > LIMITS["thin"]:
            problems.append(f"{m['id']}: {st['thin']:.0%} of its surface is thinner than 3% of its size (wires, bones, "
                            f"thin walls); the limit is {LIMITS['thin']:.0%}")
    for c in cats:
        if not any(m["category"] == c for m in idx["models"]):
            problems.append(f"category {c} is empty")
    simple = set(data["catalogue"].get("simple_shapes", []))
    for m in idx["models"]:
        if m["category"] == "shapes" and (m["kind"] == "bundled") != (m["id"] in simple):
            problems.append(f"{m['id']}: the simple shapes ship in the app and only they (catalogue.simple_shapes)")
    for m in data["model"]:
        if not m.get("generator") and not m.get("commons") and not m.get("license_evidence"):  # (copies inherit)
            problems.append(f"{m['id']}: no licence evidence")
    bundled = sum(m.get("bytes", 0) for m in idx["models"] if m["kind"] == "bundled")
    if bundled > 45_000_000:
        problems.append(f"the starter set is {bundled / 1e6:.0f} MB (keep it under 45 MB)")
    for p in problems:
        print("  PROBLEM", p)
    remote = sum(m.get("bytes", 0) for m in idx["models"])
    print(f"  check: {len(idx['models'])} models, starter set {bundled / 1e6:.1f} MB, all files {remote / 1e6:.0f} MB, "
          f"{len(problems)} problems")
    return problems


# ----------------------------------------------------------------------------- review sheet and publishing
def sheet(data) -> None:
    from PIL import Image, ImageDraw
    qa = ROOT / "build" / "qa" / "catalogue"
    qa.mkdir(parents=True, exist_ok=True)
    for c in data["category"]:
        ms = [m for m in data["model"] if m["category"] == c["id"]]
        cols, cell = 6, 200
        rows = (len(ms) + cols - 1) // cols
        img = Image.new("RGBA", (cols * cell, rows * (cell + 24)), (38, 38, 42, 255))
        d = ImageDraw.Draw(img)
        for i, m in enumerate(ms):
            x, y = (i % cols) * cell, (i // cols) * (cell + 24)
            p = PKG / "thumbs" / f"{m['id']}.png"
            if p.is_file():
                img.alpha_composite(Image.open(p).convert("RGBA").resize((cell, cell)), (x, y))
            d.text((x + 6, y + cell + 4), m["id"][:30], fill=(220, 220, 220, 255))
        img.save(qa / f"sheet-{c['id']}.png")
        print(f"  sheet: {qa / ('sheet-' + c['id'] + '.png')}")


def publish(data) -> None:
    tag = data["catalogue"]["base_url"].rstrip("/").rsplit("/", 1)[1]
    files = sorted(str(p) for p in OUT.glob("*.ply.gz"))
    exists = subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode == 0
    if not exists:
        # a pre-release: never the latest release, so GitHub's /releases/latest and the app's update check skip it
        subprocess.run(["gh", "release", "create", tag, "--prerelease", "--latest=false",
                        "--title", f"Model catalogue files ({tag}), not an app version", "--notes",
                        "**Not an app release.** The 3D models of the app's Model catalogue, one `.ply.gz` per model, "
                        "downloaded by the app on first use and checked against the SHA-256 in its index. Do not "
                        "delete it: the apps that list these models download from it. Credits and licences: "
                        "src/atlas_toolkit/licenses/catalogue-models.txt."], check=True)
    for i in range(0, len(files), 20):
        subprocess.run(["gh", "release", "upload", tag, "--clobber", *files[i:i + 20]], check=True)
    print(f"  publish: {len(files)} files to {tag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["all", "fetch", "prepare", "thumbs", "index", "bundle", "check", "sheet",
                                     "publish"])
    ap.add_argument("--only", help="comma-separated model ids")
    ap.add_argument("--force", action="store_true", help="thumbs: render again even when unchanged")
    ap.add_argument("--partial", action="store_true", help="index: leave out models not prepared yet (development)")
    a = ap.parse_args()
    data = recipes()
    steps = ["fetch", "prepare", "thumbs", "index", "bundle", "check"] if a.step == "all" else [a.step]
    for step in steps:
        print(f"[{step}]")
        if step == "fetch":
            fetch(data, a.only)
        elif step == "prepare":
            prepare(data, a.only)
        elif step == "thumbs":
            thumbs(data, a.only, a.force)
        elif step == "index":
            index(data, a.partial)
        elif step == "bundle":
            bundle(data)
        elif step == "check":
            if check(data):
                return 1
        elif step == "sheet":
            sheet(data)
        elif step == "publish":
            publish(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
