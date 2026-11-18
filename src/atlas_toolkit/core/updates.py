"""
Is a newer public release out? The app asks atlas-toolkit.org for the latest public version number, at most once a
day and only when the person said yes (prefs `update_check`: ask | daily | never; "ask" until they answer). No Qt:
the window, the command line and the tests share it.

The request is a plain GET with no identifier, no version and nothing about the person or the computer: the site
answers {"version": "X.Y.Z"} and the comparison happens here. If the site does not answer, GitHub's latest release
is asked instead. Any failure is silent: a check that cannot reach the network simply finds nothing.

App state in app.json (this computer's, never exported):
    update_last     when the last check finished (epoch seconds), whether or not it found anything
    update_latest   the newest version it saw
    update_skip     a version the person chose to skip: it is not offered again, a later one is

`ATLAS_TOOLKIT_NO_UPDATE_CHECK=1` turns checking off whatever the setting says (tests, kiosks, packagers).
"""
from __future__ import annotations

import json
import os
import re
import time

from ..version import __version__

SITE_URL = "https://atlas-toolkit.org/download/?format=json"
GITHUB_URL = "https://api.github.com/repos/atlas-toolkit-org/AtlasToolkit/releases/latest"
DOWNLOAD_URL = "https://atlas-toolkit.org/download/"
NOTES_URL = "https://github.com/atlas-toolkit-org/AtlasToolkit/releases/tag/v{version}"
AGENT = "atlas-toolkit-update-check"        # the same for everyone: no version, no identifier
DAY = 24 * 3600
ENV_OFF = "ATLAS_TOOLKIT_NO_UPDATE_CHECK"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse(version) -> tuple[int, int, int] | None:
    """(major, minor, patch) for "X.Y.Z" or "vX.Y.Z"; None for anything else (pre-releases included)."""
    m = _VERSION.match(str(version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(remote, local: str = __version__) -> bool:
    """True when `remote` is a later release than `local`. An early-access build ahead of the public one never is."""
    r, here = parse(remote), parse(local)
    return bool(r and here and r > here)


def disabled() -> bool:
    return os.environ.get(ENV_OFF, "").strip() not in ("", "0")


def _get_json(url: str, timeout: float):
    from urllib import error as urlerror
    from urllib import request as urlrequest
    from .download import contexts as _contexts           # the default store, then certifi's, then the system files
    import ssl
    last = None
    for ctx in _contexts():
        try:
            req = urlrequest.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
            with urlrequest.urlopen(req, timeout=timeout, context=ctx) as r:
                return json.loads(r.read(64 * 1024).decode("utf-8"))
        except urlerror.URLError as e:
            if not isinstance(e.reason, ssl.SSLCertVerificationError):
                raise
            last = e
    raise last


def latest(timeout: float = 8.0, fetch=None) -> str | None:
    """The newest public version, from the site or else GitHub; None when neither answers with one."""
    fetch = fetch or _get_json
    for url, key in ((SITE_URL, "version"), (GITHUB_URL, "tag_name")):
        try:
            data = fetch(url, timeout)
        except Exception:                                   # offline, a proxy, a timeout, bad JSON: try the next
            continue
        v = data.get(key) if isinstance(data, dict) else None
        if parse(v):
            return str(v).lstrip("v")
    return None


def due(settings, now: float | None = None) -> bool:
    """A daily check is on, not turned off by the environment, and the last one is a day old or more."""
    from . import prefs
    if disabled() or prefs.get(settings, "update_check") != "daily":
        return False
    last = settings.get("update_last", 0)
    last = last if isinstance(last, (int, float)) and not isinstance(last, bool) else 0
    now = time.time() if now is None else now
    return now - last >= DAY or last > now                  # a clock set back does not stop checking


def record(settings, version: str | None, now: float | None = None) -> None:
    """Note a finished check (found or not), so the next one waits a day."""
    settings.set("update_last", now if now is not None else time.time())
    if version:
        settings.set("update_latest", version)
    settings.save()


def offer(settings, local: str = __version__) -> str | None:
    """The version to tell the person about: newer than this one and not skipped; None otherwise."""
    v = settings.get("update_latest")
    if not isinstance(v, str) or not newer(v, local) or v == settings.get("update_skip"):
        return None
    return v


def check(settings, now: float | None = None, fetch=None, local: str = __version__) -> str | None:
    """Ask for the latest version now (whatever the schedule), remember it, and return what to offer."""
    record(settings, latest(fetch=fetch), now)
    return offer(settings, local)
