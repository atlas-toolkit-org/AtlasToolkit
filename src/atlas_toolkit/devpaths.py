"""
Optional folders on the developer's machine, for tests and dev scripts only (the app never reads them).

Some tests compare against material that is not part of this repository: the studio workspace's reference models
(3d_models_db), the original Blender pieces (projects/blender3d_project) and the legacy scripts the tools replaced
(code/Python/_old). Say where they are in dev.local.toml at the repository root (gitignored; copy
dev.local.example.toml) or with environment variables, which win:

    ATLAS_STUDIO_WORKSPACE   the studio workspace folder
    ATLAS_DESIGN_DIR         the studio design system (default: <workspace>/studio/design)
    ATLAS_PRESETS_DIR        the private preset packs repository (AtlasToolkit-presets), the source of the official
                             packs: the app ships with no presets, so tests of real presets need it

Without them those tests skip, saying why, and dev scripts ask for paths on their command line.
"""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL_CONFIG = REPO / "dev.local.toml"
# A font every platform has, for labels on validation sheets: the app's own Geist.
DEV_FONT = str(Path(__file__).resolve().parent / "ui" / "theme" / "fonts" / "Geist-Regular.ttf")
_NO_WORKSPACE = Path("STUDIO_WORKSPACE_NOT_SET (see dev.local.example.toml)")


@lru_cache(maxsize=None)
def _local() -> dict:
    try:
        return tomllib.loads(LOCAL_CONFIG.read_text(encoding="utf-8")).get("paths", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _folder(env: str, key: str) -> Path | None:
    value = os.environ.get(env) or _local().get(key)
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.is_dir() else None


def workspace() -> Path | None:
    """The studio workspace, or None when not configured or not on this machine."""
    return _folder("ATLAS_STUDIO_WORKSPACE", "workspace")


def studio_workspace() -> Path:
    """For dev scripts' defaults: the workspace, or a path whose name says how to set it (files under it are
    missing, so a script that needs them stops with that name in its error)."""
    return workspace() or _NO_WORKSPACE


def workspace_path(*parts: str) -> Path | None:
    """A path inside the studio workspace (not checked for existence), or None without a workspace."""
    ws = workspace()
    return ws.joinpath(*parts) if ws else None


def design_dir() -> Path | None:
    """The studio design system that scripts/sync_design.py copies into ui/theme."""
    found = _folder("ATLAS_DESIGN_DIR", "design")
    if found:
        return found
    candidate = workspace_path("studio", "design")
    return candidate if candidate and candidate.is_dir() else None


def presets_dir() -> Path | None:
    """The private preset packs repository, or None. Its packs/ folder holds one source folder per pack."""
    return _folder("ATLAS_PRESETS_DIR", "presets")


def preset_packs() -> list[Path]:
    """The pack source folders in the private repository (each holds a pack.json), or [] without it."""
    root = presets_dir()
    if root is None or not (root / "packs").is_dir():
        return []
    return sorted(p for p in (root / "packs").iterdir() if (p / "pack.json").is_file())


NO_PRESETS = "This needs the private preset packs: set presets in dev.local.toml (or ATLAS_PRESETS_DIR)."


@lru_cache(maxsize=None)
def _dev_presets() -> dict:
    """{tool id: {name: Preset}} from the private packs, in pack order (one per uid)."""
    from .core.presets import read_pack
    out: dict = {}
    seen = set()
    for folder in preset_packs():
        for p in read_pack(folder).presets:
            if p.uid not in seen:
                seen.add(p.uid)
                out.setdefault(p.tool, {})[p.name] = p
    return out


def preset_names(tool) -> list[str]:
    """For dev scripts: the names of `tool`'s presets in the private packs; exits with how to set them up."""
    if not preset_packs():
        raise SystemExit(NO_PRESETS)
    return list(_dev_presets().get(tool.id, {}))


def preset_raw(tool, name: str) -> dict:
    """The keys preset `name` sets on purpose, as written (what tool specs held before 2.1)."""
    if not preset_packs():
        raise SystemExit(NO_PRESETS)
    p = _dev_presets().get(tool.id, {}).get(name)
    if p is None:
        raise SystemExit(f"The private packs have no preset {name!r} for {tool.id}.")
    return {k: p.values[k] for k in p.explicit if k in p.values}


def preset_raws(tool) -> dict:
    return {name: preset_raw(tool, name) for name in preset_names(tool)}


def preset_values(tool, name: str, **overrides):
    """The values preset `name` gives `tool`, with overrides, validated."""
    from .core.presets import apply
    if not preset_packs():
        raise SystemExit(NO_PRESETS)
    p = _dev_presets().get(tool.id, {}).get(name)
    if p is None:
        raise SystemExit(f"The private packs have no preset {name!r} for {tool.id}.")
    values = dict(apply(tool, p)[0])
    values.update(overrides)
    return tool.values(values)


def preset_dirs_env() -> dict:
    """Environment for a child app or CLI run that should offer the private packs (empty without them)."""
    packs = preset_packs()
    return {"ATLAS_TOOLKIT_PRESET_DIRS": os.pathsep.join(str(p) for p in packs)} if packs else {}


def qa_dir(name: str) -> Path:
    """Output folder for a dev or validation script: build/qa/<name> in the repository (gitignored)."""
    out = REPO / "build" / "qa" / name
    out.mkdir(parents=True, exist_ok=True)
    return out
