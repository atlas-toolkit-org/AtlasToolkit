"""
Downloads over HTTPS, shared by the depth environment's installer (geom/depth_runtime.py), the update check
(core/updates.py) and the model catalogue (core/catalogue.py). No Qt.

- open_url():  a urllib response, trying the default certificate store first; a frozen app's OpenSSL may look where
               the build machine kept its certificates, so then certifi's, then the usual system files.
- fetch():     streams a file to disk with its SHA-256 checked, reporting progress and honouring a cancel flag; the
               file appears under its name only once it is whole and verified.
"""
from __future__ import annotations

import hashlib
import os
import ssl
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem",
            "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem")
AGENT = "atlas-toolkit-installer"


class DownloadError(Exception):
    """kind: network | hash | cancelled. The message is ready to show."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def contexts():
    """The default context first, then certifi's, then the usual system files."""
    yield ssl.create_default_context()
    try:
        import certifi
        yield ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for f in CA_FILES:
        if Path(f).is_file():
            yield ssl.create_default_context(cafile=f)


def open_url(url: str, method: str = "GET", timeout: float = 30, agent: str = AGENT):
    last = None
    for ctx in contexts():
        try:
            req = urlrequest.Request(url, method=method, headers={"User-Agent": agent})
            return urlrequest.urlopen(req, timeout=timeout, context=ctx)
        except urlerror.URLError as e:
            if not isinstance(e.reason, ssl.SSLCertVerificationError):
                raise
            last = e
    raise last


def fetch(url: str, dest: Path, sha256: str | None = None, size: int | None = None, progress=None,
          cancelled=None, timeout: float = 60, opener=None) -> Path:
    """Stream `url` to `dest`. `progress(done_bytes, total_bytes)` follows it; `cancelled()` returning True stops it.
    With `sha256` a file whose digest differs is deleted and DownloadError("hash") raised. `opener` replaces
    open_url (tests)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    got = 0
    try:
        with (opener or open_url)(url, timeout=timeout) as r, part.open("wb") as out:
            total = size or int(getattr(r, "headers", {}).get("Content-Length", 0) or 0)
            while chunk := r.read(1 << 16):
                if cancelled and cancelled():
                    raise DownloadError("cancelled", "The download was cancelled.")
                out.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    except DownloadError:
        part.unlink(missing_ok=True)
        raise
    except (urlerror.URLError, OSError, TimeoutError, ValueError) as e:
        part.unlink(missing_ok=True)
        reason = getattr(e, "reason", None) or e
        raise DownloadError("network", f"The download failed ({reason}). Check the internet connection, a proxy "
                                       "(HTTPS_PROXY) or a firewall, then try again.") from None
    if sha256 and digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError("hash", "The downloaded file is not the expected one (its SHA-256 differs). Try again "
                                    "later or on another network.")
    os.replace(part, dest)
    return dest
