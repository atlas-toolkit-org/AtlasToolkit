"""
The publishers' keys this app trusts to sign preset packs (core/presets.py, docs/PRESETS.md#signing).

A pack signed by one of these keys, and unchanged since, shows as Official. Each key has an id (the pack's
signature.json names it), its Ed25519 public key in hex, and a status:

    active    signs new packs
    retired   no longer signs, but the packs it signed stay Official
    revoked   no longer trusted (the private key was lost or leaked): its packs show "Key revoked"

Rotating a key is an app release: add the new key, mark the old one retired. Never remove a key that signed packs
people own. Only public keys belong here; the private keys stay offline, outside every repository.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublisherKey:
    public: str                  # 64 hex characters
    publisher: str
    status: str = "active"       # active / retired / revoked


KEYS: dict[str, PublisherKey] = {
    # made by AtlasToolkit-presets/tools/build_pack.py keygen (2026-11-09)
    "atlas-presets-1": PublisherKey("e0e7fbdaef0a1ff47d20da48f6a5039a564278d0f9a45cd0b8d66d754a9e931a", "Atlas Toolkit"),
}
