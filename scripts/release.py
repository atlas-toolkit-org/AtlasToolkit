"""
A public release in five steps, each one checked, with you reviewing between them. Standard library + git + gh.

    python scripts/release.py check   [X.Y.Z]   read-only: what a release of X.Y.Z still needs (the default: this version)
    python scripts/release.py prepare  X.Y.Z    the version, the changelog date, the install guides; a branch, a commit
                                                (signed off), a pull request. Review it; CI runs on it; you merge it.
    python scripts/release.py tag      X.Y.Z    after the merge: tag main and push the tag. release.yml then builds every
                                                installer and makes a DRAFT release (nothing public yet).
    python scripts/release.py status   X.Y.Z    the release run, and the draft's files and checksums once it is there.
    python scripts/release.py notes    X.Y.Z    the draft's notes again, from CHANGELOG.md as it is now (after a
                                                changelog fix merged once the tag was out).
    python scripts/release.py publish  X.Y.Z    after you have tried the draft: publish it on GitHub as the latest
                                                release, then the website: fill public/X.Y.Z/ on the server and
                                                update the site's version texts (both shown first, then asked).

Every step that changes something outside this checkout asks first (--yes skips the questions, for when you have
read them). The website's parts read `[release]` in dev.local.toml:

    [release]
    site = "C:/path/to/atlas-toolkit.org"        # the website's checkout (its roadmap text, its deploy)
    ssh = "account@ssh-account.alwaysdata.net"   # the server, for filling public/X.Y.Z/ from the GitHub release

Without them those parts print what to run instead. See docs/PACKAGING.md, "Releasing".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "atlas_toolkit" / "version.py"
CHANGELOG = ROOT / "CHANGELOG.md"
INSTALL_DOCS = [ROOT / "docs" / "install" / n for n in ("windows.md", "linux.md", "macos.md")]
REPO = "atlas-toolkit-org/AtlasToolkit"
# git over HTTPS through gh's credentials: this works where the checkout's SSH remote does not
PUSH = ["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", "push",
        f"https://github.com/{REPO}.git"]
INSTALLERS = ("AtlasToolkit-{v}-windows-x64-setup.exe", "atlas-toolkit_{v}_amd64.deb", "atlas-toolkit-{v}-1.x86_64.rpm",
              "AtlasToolkit-{v}-linux-x86_64.AppImage", "AtlasToolkit-{v}-macos-arm64.dmg")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# The server step: the GitHub release's files into public/X.Y.Z/, every checksum verified before the sums file goes
# in place under its real name (which is what makes the version live on /download/). It runs on the server.
SERVER_FILL = r"""set -eu
v="$1"; dest="$HOME/private/atlas-toolkit.org/public/$v"
[ -f "$dest/SHA256SUMS.txt" ] && { echo "public/$v is already live"; exit 0; }
mkdir -p "$dest" && cd "$dest"
gh_dl="https://github.com/atlas-toolkit-org/AtlasToolkit/releases/download/v$v"
for f in AtlasToolkit-$v-windows-x64-setup.exe atlas-toolkit_${v}_amd64.deb atlas-toolkit-$v-1.x86_64.rpm \
         AtlasToolkit-$v-linux-x86_64.AppImage AtlasToolkit-$v-macos-arm64.dmg; do curl -fsSLO "$gh_dl/$f"; done
curl -fsSL -o sums.tmp "$gh_dl/SHA256SUMS.txt" && sha256sum -c sums.tmp && mv sums.tmp SHA256SUMS.txt
echo "public/$v is live"
"""
# what ssh runs on the server: the script arrives on stdin, any CR (a CRLF file) removed before bash reads it
REMOTE_FILL = "tr -d '\\r' | bash -s -- {v}"


# ----------------------------------------------------------------------------- small helpers
def run(cmd, check=True, capture=False, cwd=ROOT, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                       capture_output=capture, **kw)
    if check and r.returncode:
        sys.exit(f"failed: {' '.join(map(str, cmd))}\n{(r.stderr or '') if capture else ''}".rstrip())
    return r


def out(cmd, cwd=ROOT) -> str:
    return run(cmd, capture=True, cwd=cwd).stdout.strip()


def ask(question: str, yes: bool) -> bool:
    if yes:
        print(f"{question} yes (--yes)")
        return True
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def say(ok: bool, text: str) -> bool:
    print(("  ok    " if ok else "  TODO  ") + text)
    return ok


def app_version() -> str:
    return re.search(r'__version__ = "([^"]+)"', VERSION_FILE.read_text(encoding="utf-8"))[1]


def changelog_heading(version: str) -> str | None:
    m = re.search(rf"^## {re.escape(version)}\b[^\n]*$", CHANGELOG.read_text(encoding="utf-8"), re.M)
    return m[0] if m else None


def local_config() -> dict:
    f = ROOT / "dev.local.toml"
    try:
        return tomllib.loads(f.read_text(encoding="utf-8")).get("release", {}) if f.is_file() else {}
    except tomllib.TOMLDecodeError:
        return {}


def latest_public() -> str:
    """The version of GitHub's latest (published) release."""
    tag = out(["gh", "release", "view", "--repo", REPO, "--json", "tagName", "-q", ".tagName"])
    return tag.lstrip("v")


def need_version(v: str) -> str:
    if not SEMVER.match(v):
        sys.exit(f"{v!r} is not a version like 2.4.0")
    return v


# ----------------------------------------------------------------------------- the edits prepare makes
def install_docs_for(text: str, old: str, new: str) -> str:
    """The install guides name the current version's files: swap old for new in those names, their links and the
    "current version is **X.Y.Z**" line, never in prose (what a version was tested on stays true)."""
    o = re.escape(old)
    pats = [rf"(AtlasToolkit-){o}(-)", rf"(atlas-toolkit_){o}(_)", rf"(atlas-toolkit-){o}(-1\.)", rf"(/v){o}(/)",
            rf"(\*\*){o}(\*\*:)"]
    for p in pats:
        text = re.sub(p, rf"\g<1>{new}\g<2>", text)
    return text


def dated_changelog(text: str, version: str, day: str) -> str:
    return re.sub(rf"^## {re.escape(version)}(?: \([^)]*\))?[ \t]*$", f"## {version} ({day})", text, count=1, flags=re.M)


# ----------------------------------------------------------------------------- steps
def check(version: str) -> bool:
    print(f"Release {version}:")
    good = True
    good &= say(app_version() == version, f"src/atlas_toolkit/version.py says {app_version()}")
    head = changelog_heading(version)
    good &= say(bool(head), f"CHANGELOG.md has an entry for {version}" + (f": {head[3:]}" if head else ""))
    public = latest_public()
    for f in INSTALL_DOCS:
        t = f.read_text(encoding="utf-8")
        good &= say(f"**{version}**:" in t, f"{f.relative_to(ROOT)} names {version} as the current version")
    good &= say(tuple(map(int, version.split("."))) > tuple(map(int, public.split("."))),
                f"newer than the latest public release ({public})")
    tags = out(["git", "tag", "-l", f"v{version}"])
    say(not tags, f"no tag v{version} yet" if not tags else f"tag v{version} exists (step tag is done)")
    notes = run([sys.executable, str(ROOT / "scripts" / "release_notes.py"), f"v{version}"], check=False,
                capture=True)
    good &= say(notes.returncode == 0, "scripts/release_notes.py makes the notes (what release.yml checks first)")
    return good


def prepare(version: str, yes: bool, base: str) -> None:
    if out(["git", "status", "--porcelain"]):
        sys.exit("the checkout has uncommitted changes: commit or stash them first (the release commit holds only "
                 "the release edits)")
    if not changelog_heading(version):
        sys.exit(f"write the CHANGELOG.md entry first: a '## {version}' section at the top")
    old = latest_public()
    day = dt.date.today().isoformat()
    branch = f"release/{version}"
    if out(["git", "rev-parse", "--abbrev-ref", "HEAD"]) != branch:
        run(["git", "switch", "-c", branch, base])
    VERSION_FILE.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"',
                                   VERSION_FILE.read_text(encoding="utf-8")), encoding="utf-8")
    CHANGELOG.write_text(dated_changelog(CHANGELOG.read_text(encoding="utf-8"), version, day), encoding="utf-8")
    for f in INSTALL_DOCS:
        f.write_text(install_docs_for(f.read_text(encoding="utf-8"), old, version), encoding="utf-8")
    if not check(version):
        sys.exit("fix what is listed above, then run prepare again")
    print("\n" + out(["git", "diff", "--stat"]))
    if not out(["git", "status", "--porcelain"]):
        print("nothing to change: the release edits are already in")
    else:
        run(["git", "commit", "-s", "-am", f"{version}: version, changelog date and install guides"])
    if not ask(f"Push {branch} and open the pull request?", yes):
        print(f"Kept locally. Later: python scripts/release.py prepare {version}")
        return
    run(PUSH + [f"{branch}:{branch}"])
    exists = run(["gh", "pr", "view", branch, "--repo", REPO, "--json", "url", "-q", ".url"], check=False,
                 capture=True)
    if exists.returncode == 0:
        print(f"pull request: {exists.stdout.strip()}")
        return
    body = (f"Release {version}.\n\nThe changelog entry is the release notes "
            f"(`python scripts/release_notes.py v{version}` shows them). After the merge: "
            f"`python scripts/release.py tag {version}`.\n")
    print(out(["gh", "pr", "create", "--repo", REPO, "--base", "main", "--head", branch,
               "--title", f"Release {version}", "--body", body]))


def tag(version: str, yes: bool) -> None:
    run(["git", "fetch", f"https://github.com/{REPO}.git", "main"])
    main = out(["git", "rev-parse", "FETCH_HEAD"])
    shown = out(["git", "show", f"{main}:src/atlas_toolkit/version.py"])
    if f'__version__ = "{version}"' not in shown:
        sys.exit(f"main is not {version} yet: merge the release pull request first")
    if out(["git", "tag", "-l", f"v{version}"]):
        sys.exit(f"v{version} exists already")
    print(f"main is at {main[:9]}: {out(['git', 'log', '-1', '--format=%s', main])}")
    if not ask(f"Tag {main[:9]} as v{version} and push it (release.yml then builds the draft)?", yes):
        return
    run(["git", "tag", "-a", f"v{version}", main, "-m", f"Atlas Toolkit {version}"])
    run(PUSH + [f"v{version}"])
    print(f"pushed v{version}. Follow it: python scripts/release.py status {version}")


def status(version: str) -> None:
    runs = out(["gh", "run", "list", "--repo", REPO, "--workflow", "release.yml", "--branch", f"v{version}",
                "--json", "status,conclusion,url,createdAt", "--limit", "1"])
    r = json.loads(runs or "[]")
    if r:
        print(f"release run: {r[0]['status']} {r[0].get('conclusion') or ''} {r[0]['url']}")
    else:
        print("no release run yet (has the tag been pushed?)")
    view = run(["gh", "release", "view", f"v{version}", "--repo", REPO, "--json", "isDraft,assets,url"],
               check=False, capture=True)
    if view.returncode:
        print("no draft yet")
        return
    d = json.loads(view.stdout)
    names = {a["name"]: a["size"] for a in d["assets"]}
    print(f"{'draft' if d['isDraft'] else 'PUBLISHED'}: {d['url']}")
    for n in [p.format(v=version) for p in INSTALLERS] + ["SHA256SUMS.txt"]:
        print(f"  {'ok ' if n in names else 'MISSING'} {n}" + (f"  {names[n] / 1e6:.0f} MB" if n in names else ""))


def notes(version: str, yes: bool) -> None:
    """The draft's notes made again from this checkout's CHANGELOG.md (pull main first)."""
    text = out([sys.executable, str(ROOT / "scripts" / "release_notes.py"), f"v{version}"])
    print(text[:1500] + ("\n…" if len(text) > 1500 else ""))
    if ask(f"Replace the notes of v{version} with these?", yes):
        run(["gh", "release", "edit", f"v{version}", "--repo", REPO, "--notes-file", "-"], input=text)
        print("notes replaced")


def publish(version: str, yes: bool) -> None:
    view = json.loads(out(["gh", "release", "view", f"v{version}", "--repo", REPO, "--json", "isDraft,assets,url"]))
    names = {a["name"] for a in view["assets"]}
    missing = [n for n in [p.format(v=version) for p in INSTALLERS] + ["SHA256SUMS.txt"] if n not in names]
    if missing:
        sys.exit(f"the release lacks {missing}: wait for the run, or look at it (status)")
    cfg = local_config()
    # 1. GitHub
    if view["isDraft"]:
        if ask(f"Publish {view['url']} as the latest release (public from now on)?", yes):
            run(["gh", "release", "edit", f"v{version}", "--repo", REPO, "--draft=false", "--latest"])
            print("published on GitHub")
        else:
            return
    else:
        print("already published on GitHub")
    # 2. The server's public/X.Y.Z/ (what /download/ offers)
    host = cfg.get("ssh")
    script = ROOT / "build" / "server-fill.sh"
    script.parent.mkdir(exist_ok=True)
    script.write_bytes(SERVER_FILL.encode("utf-8"))           # LF only: bash reads "set -eu\r" as a bad option
    if host and ask(f"Fill public/{version}/ on {host} from the GitHub release (checksums verified)?", yes):
        fill_server(host, version)
    elif not host:
        print(f"\nOn the server (set [release] ssh in dev.local.toml to do this from here), from this folder:\n"
              f"  ssh <account>@ssh-<account>.alwaysdata.net \"{REMOTE_FILL.format(v=version)}\" "
              f"< build/server-fill.sh")
    # 3. The website's texts
    site_texts(version, cfg, yes)


def fill_server(host: str, version: str) -> None:
    """SERVER_FILL over ssh. Sent as bytes: in text mode Windows writes CRLF into the pipe, and the server strips any
    CR anyway (tr), so a script that picked up CRLF on the way still runs."""
    r = subprocess.run(["ssh", host, REMOTE_FILL.format(v=version)], input=SERVER_FILL.encode("utf-8"))
    if r.returncode:
        sys.exit(f"the server step failed (exit {r.returncode}): see above; nothing is live until SHA256SUMS.txt "
                 f"is in place, so it can be run again")


def site_texts(version: str, cfg: dict, yes: bool) -> None:
    """The website's side: its branch release/X.Y.Z (changes made ahead for this release) is merged; failing that, the
    roadmap's "You are here: **X.Y**, in the making" becomes "You are here: **X.Y**"; any other version its pages
    name is listed for you to look at."""
    site = Path(cfg.get("site", "")) if cfg.get("site") else None
    if not site or not (site / "dev" / "roadmap-text.md").is_file():
        print("\nThe website's checkout is not set ([release] site in dev.local.toml): check its roadmap caption "
              f"(dev/roadmap-text.md) for '{version.rsplit('.', 1)[0]}, in the making' by hand.")
        return
    # the website's own changes for this release, made ahead on a branch of the same name: merged now
    branch = f"release/{version}"
    if run(["git", "rev-parse", "--verify", "--quiet", branch], check=False, capture=True, cwd=site).returncode == 0:
        print("\nThe website's release branch:\n" + out(["git", "log", "--oneline", f"main..{branch}"], cwd=site))
        if ask(f"Website: merge {branch} into its main?", yes):
            run(["git", "switch", "main"], cwd=site)
            run(["git", "merge", "--ff-only", branch], cwd=site)
            print("merged. Deploy as the website's README says, e.g.\n"
                  "  rsync -av site/ <account>@ssh-<account>.alwaysdata.net:sites/atlas-toolkit.org/")
    minor = version.rsplit(".", 1)[0]
    rm = site / "dev" / "roadmap-text.md"
    text = rm.read_text(encoding="utf-8")
    making = f"You are here: **{minor}**, in the making"
    if making in text and ask(f"Website: change the roadmap caption to 'You are here: **{minor}**'?", yes):
        rm.write_text(text.replace(making, f"You are here: **{minor}**"), encoding="utf-8")
        run([sys.executable, "dev/scripts/roadmap.py"], cwd=site)
        run(["git", "commit", "-am", f"Roadmap: {minor} is out"], cwd=site)
        print(f"committed in the website's checkout. Deploy the page as its README says, e.g.\n"
              f"  rsync -av site/ <account>@ssh-<account>.alwaysdata.net:sites/atlas-toolkit.org/")
    old = re.compile(r"\b2\.\d+\.\d+\b")
    hits = []
    for f in (site / "site").rglob("*.html"):
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in old.finditer(line):
                if m[0] != version:
                    hits.append(f"  {f.relative_to(site)}:{n}: {m[0]}")
    print("\nVersions named on the website's pages (look whether they should move):" if hits else
          "\nNo other version is named on the website's pages.")
    print("\n".join(hits[:30]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["check", "prepare", "tag", "status", "notes", "publish"])
    ap.add_argument("version", nargs="?")
    ap.add_argument("--yes", action="store_true", help="answer yes to every question (you have read them)")
    ap.add_argument("--base", default="origin/main", help="prepare: the branch to start from (default origin/main)")
    a = ap.parse_args()
    if not shutil.which("gh"):
        sys.exit("needs the GitHub CLI (gh), signed in")
    v = need_version(a.version or app_version())
    if a.step == "check":
        return 0 if check(v) else 1
    {"prepare": lambda: prepare(v, a.yes, a.base), "tag": lambda: tag(v, a.yes), "status": lambda: status(v),
     "notes": lambda: notes(v, a.yes), "publish": lambda: publish(v, a.yes)}[a.step]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
