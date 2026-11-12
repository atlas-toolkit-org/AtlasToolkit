"""Atomic network persistence, including FlowRep's OBJ edge/chain convention."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import numpy as np

from .types import CurveNetwork, FlowRepError

SCHEMA = "atlas-flowrep-network"
SCHEMA_VERSION = 1


def _json_default(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, Path):
        return str(x)
    raise TypeError(type(x).__name__)


def json_text(value):
    return json.dumps(value, indent=2, default=_json_default, allow_nan=False, ensure_ascii=False)


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_npz(path, **arrays):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as stream:
            np.savez_compressed(stream, **arrays)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _pack(sequences):
    sizes = [len(x) for x in sequences]
    return (np.concatenate(sequences).astype(np.int64) if sum(sizes) else np.array([], np.int64),
            np.cumsum([0] + sizes, dtype=np.int64))


def _unpack(flat, offsets):
    offsets = np.asarray(offsets, np.int64)
    if len(offsets) == 0 or offsets[0] != 0 or offsets[-1] != len(flat) or np.any(np.diff(offsets) < 0):
        raise FlowRepError("invalid ragged-array offsets in network")
    return [flat[a:b].copy() for a, b in zip(offsets[:-1], offsets[1:])]


def save_network(network: CurveNetwork, path):
    path = Path(path)
    header = {"schema": SCHEMA, "version": SCHEMA_VERSION, "kinds": network.kinds,
              "strand_ids": network.strand_ids, "diagnostics": network.diagnostics, "metadata": network.metadata}
    if path.suffix.lower() == ".obj":
        lines = ["# atlas-toolkit:flowrep-sketch 0.1.0"]
        lines += ["v " + " ".join(f"{x:.12g}" for x in p) for p in network.vertices]
        chains, ei = [], 1
        for curve in network.curves:
            chain = []
            for a, b in zip(curve[:-1], curve[1:]):
                lines.append(f"l {a+1} {b+1}")
                chain.append(ei)
                ei += 1
            chains.append("# chain " + " ".join(map(str, chain)))
        atomic_text(path, "\n".join(lines + chains) + "\n")
    elif path.suffix.lower() == ".npz":
        flat, offsets = _pack(network.curves)
        edge_flat, edge_offsets = _pack(network.edge_ids)
        atomic_npz(path, vertices=network.vertices, curves=flat, offsets=offsets,
                   edge_ids=edge_flat, edge_offsets=edge_offsets, header=np.array(json_text(header)))
    elif path.suffix.lower() == ".json":
        atomic_text(path, json_text(dict(header, vertices=network.vertices, curves=network.curves,
                                        edge_ids=network.edge_ids)) + "\n")
    else:
        raise FlowRepError("network output must end in .json, .npz, or .obj")
    return path


def _chain_edges(edges):
    """Orient an explicitly ordered sequence without assuming the first edge's direction."""
    for reverse in (False, True):
        chain = list(edges[0][::-1] if reverse else edges[0])
        for a, b in edges[1:]:
            if chain[-1] == a:
                chain.append(b)
            elif chain[-1] == b:
                chain.append(a)
            else:
                break
        else:
            return np.asarray(chain, np.int64)
    raise FlowRepError("OBJ # chain contains disconnected or unordered edges")


def load_obj_network(path):
    V, E, chains = [], [], []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        t = line.split()
        try:
            if t and t[0] == "v":
                V.append([float(x) for x in t[1:4]])
            elif t and t[0] == "l":
                ids = [int(x.split("/")[0]) for x in t[1:]]
                if 0 in ids:
                    raise ValueError("OBJ indices are 1-based")
                ids = [x-1 if x > 0 else len(V)+x for x in ids]
                E.extend(zip(ids[:-1], ids[1:]))
            elif len(t) > 2 and t[:2] == ["#", "chain"]:
                chains.append([int(x)-1 for x in t[2:]])
        except (ValueError, IndexError) as exc:
            raise FlowRepError(f"malformed network OBJ at line {number}: {exc}") from exc
    if not E:
        raise FlowRepError("network OBJ contains no line elements")
    E = np.asarray(E, np.int64)
    if np.any(E < 0) or np.any(E >= len(V)):
        raise FlowRepError("OBJ line references an invalid vertex")
    if chains:
        if any(min(c) < 0 or max(c) >= len(E) for c in chains):
            raise FlowRepError("OBJ # chain references an invalid line")
        curves = [_chain_edges(E[c]) for c in chains]
        covered = set(x for c in chains for x in c)
        curves += [e.copy() for j, e in enumerate(E) if j not in covered]
    else:
        from .flowlines import graph_chains
        curves, _ = graph_chains(E, np.arange(len(E)))
    return CurveNetwork(V, curves, metadata={"source_network": str(Path(path).resolve()), "imported": True})


def load_network(path) -> CurveNetwork:
    path = Path(path)
    if path.suffix.lower() == ".obj":
        return load_obj_network(path)
    try:
        if path.suffix.lower() == ".npz":
            with np.load(path, allow_pickle=False) as d:
                header = json.loads(str(d["header"].item()))
                V = d["vertices"].copy()
                curves = _unpack(d["curves"], d["offsets"])
                edges = _unpack(d["edge_ids"], d["edge_offsets"])
        else:
            header = json.loads(path.read_text(encoding="utf-8"))
            V, curves, edges = header["vertices"], header["curves"], header.get("edge_ids", [])
        if header.get("schema") != SCHEMA or header.get("version") != SCHEMA_VERSION:
            raise FlowRepError("unsupported network schema/version")
        return CurveNetwork(V, curves, header.get("kinds", []), [np.asarray(e, np.int64) for e in edges],
                            header.get("strand_ids", []), header.get("diagnostics", {}), header.get("metadata", {}))
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        raise FlowRepError(f"cannot read network {path.name}: {exc}") from exc

