"""
What graphics hardware this computer has, for components that can use an NVIDIA GPU. No Qt; nothing here names a tool.

`detect()` asks `nvidia-smi` first (name, driver, memory, compute capability). When there is none, or it cannot reach
a driver, it looks for NVIDIA hardware without it (the Windows registry, Linux's /sys), so a card with a missing or
broken driver is told apart from no card at all. A Mac has no NVIDIA GPU. It starts at most two short processes:
call it from a background thread, not on every page build.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import procs

NVIDIA_VENDOR = "0x10de"
QUERY = "name,driver_version,memory.total,compute_cap"
DRIVER_URL = "https://www.nvidia.com/drivers"


@dataclass(frozen=True)
class Gpu:
    name: str
    driver: str = ""                 # "595.71"; "" when unknown
    memory_mb: int | None = None
    compute: tuple | None = None     # (12, 0); None when the driver does not say
    source: str = "nvidia-smi"       # or "registry", "pci"

    @property
    def driver_number(self) -> float:
        m = re.match(r"(\d+)(?:\.(\d+))?", self.driver)
        return float(f"{m[1]}.{m[2] or 0}") if m else 0.0

    @property
    def compute_text(self) -> str:
        return f"{self.compute[0]}.{self.compute[1]}" if self.compute else "?"

    @property
    def sm(self) -> str:
        """The architecture name PyTorch lists in get_arch_list(): sm_120."""
        return f"sm_{self.compute[0]}{self.compute[1]}" if self.compute else ""


@dataclass
class GpuReport:
    status: str                      # "nvidia" (usable driver), "no-driver" (card, no working driver), "none", "apple"
    gpus: list = field(default_factory=list)
    nvidia_smi: str = ""             # the program that answered, if one did
    problem: str = ""                # why a card found has no working driver, in a sentence
    others: list = field(default_factory=list)   # other display adapters, by name, for the summary

    def best(self) -> Gpu | None:
        """The card that decides: the newest architecture, then the most memory."""
        return max(self.gpus, key=lambda g: (g.compute or (0, 0), g.memory_mb or 0), default=None)

    def summary(self) -> str:
        g = self.best()
        if self.status == "apple":
            return "Apple GPU: the depth models run on the CPU here."
        if self.status == "nvidia" and g:
            mem = f" · {g.memory_mb / 1024:.0f} GB" if g.memory_mb else ""
            return f"{g.name}{mem} · driver {g.driver or '?'} · compute capability {g.compute_text}."
        if self.status == "no-driver":
            return f"{g.name if g else 'An NVIDIA card'} was found, but {self.problem or 'it has no working NVIDIA driver'}."
        others = f" ({', '.join(self.others)})" if self.others else ""
        return f"No NVIDIA graphics card found{others}."


# ----------------------------------------------------------------------------- nvidia-smi
def smi_candidates() -> list[Path]:
    found = shutil.which("nvidia-smi")
    cands = [Path(found)] if found else []
    if sys.platform == "win32":
        cands += [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
                  Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"]
    elif sys.platform.startswith("linux"):
        cands += [Path("/usr/bin/nvidia-smi"), Path("/usr/lib/wsl/lib/nvidia-smi")]
    return list(dict.fromkeys(cands))


def run_smi(exe: Path, fields: str, timeout: float = 15) -> tuple[int, str]:
    """(exit code, output). A program that cannot start or hangs counts as failed (-1)."""
    try:
        proc = procs.spawn([str(exe), f"--query-gpu={fields}", "--format=csv,noheader,nounits"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return -1, str(e)
    try:
        out = proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        return -1, "nvidia-smi did not answer"
    finally:
        procs.finish(proc)
    return proc.returncode, out


def parse_smi(text: str, with_compute: bool = True) -> list[Gpu]:
    """Lines of `name, driver, memory[, compute_cap]` (csv, no header, no units). The name may hold commas: split from the right."""
    gpus = []
    n = 3 if with_compute else 2
    for line in text.splitlines():
        parts = [p.strip() for p in line.rsplit(",", n)]
        if len(parts) != n + 1 or not re.match(r"\d+\.\d+", parts[1]):
            continue
        memory = int(parts[2]) if parts[2].isdigit() else None
        compute = None
        if with_compute and (m := re.fullmatch(r"(\d+)\.(\d+)", parts[3])):
            compute = (int(m[1]), int(m[2]))
        gpus.append(Gpu(parts[0], parts[1], memory, compute))
    return gpus


def from_smi(run=run_smi) -> tuple[list[Gpu], str, str]:
    """(cards, the program that answered, what went wrong when none did)."""
    problem = ""
    for exe in smi_candidates():
        if not exe.is_file():
            continue
        code, out = run(exe, QUERY)
        if code == 0 and (gpus := parse_smi(out)):
            return gpus, str(exe), ""
        if code != 0 and "compute_cap" in out:          # older drivers do not know the field
            code, out = run(exe, "name,driver_version,memory.total")
            if code == 0 and (gpus := parse_smi(out, with_compute=False)):
                return gpus, str(exe), ""
        problem = (out.strip().splitlines() or [f"exit code {code}"])[0][:200]
    return [], "", problem


# ----------------------------------------------------------------------------- without nvidia-smi
DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def windows_driver(version: str) -> str:
    """Windows writes NVIDIA's 595.71 as 32.0.15.9571: the last five digits of the last two fields."""
    parts = version.split(".")
    if len(parts) != 4:
        return ""
    digits = (parts[2] + parts[3])[-5:]
    return f"{int(digits[:3])}.{digits[3:]}" if len(digits) == 5 and digits.isdigit() else ""


def windows_adapters() -> list[dict]:
    """The display adapters Windows knows, with or without a working driver: provider, description, driver version."""
    import winreg
    found = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS)
    except OSError:
        return found
    with root:
        for i in range(64):
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            if not name.isdigit():
                continue
            try:
                with winreg.OpenKey(root, name) as key:
                    item = {}
                    for value in ("ProviderName", "DriverDesc", "DriverVersion", "MatchingDeviceId"):
                        try:
                            item[value] = str(winreg.QueryValueEx(key, value)[0])
                        except OSError:
                            item[value] = ""
                    found.append(item)
            except OSError:
                continue
    return found


def linux_adapters(root: Path = Path("/sys/bus/pci/devices")) -> list[dict]:
    """PCI display controllers (class 0x03xxxx): vendor, device and the kernel driver bound to each."""
    found = []
    if not root.is_dir():
        return found
    for dev in sorted(root.iterdir()):
        try:
            if not (dev / "class").read_text(encoding="ascii").strip().startswith("0x03"):
                continue
            driver = (dev / "driver").resolve().name if (dev / "driver").exists() else ""
            found.append({"vendor": (dev / "vendor").read_text(encoding="ascii").strip().lower(),
                          "device": (dev / "device").read_text(encoding="ascii").strip().lower(), "driver": driver})
        except OSError:
            continue
    return found


def _hardware(windows=windows_adapters, linux=linux_adapters) -> tuple[list[Gpu], list[str], str]:
    """NVIDIA cards found without nvidia-smi, the other adapters by name, and why the NVIDIA ones do not work."""
    nvidia, others, problem = [], [], ""
    if sys.platform == "win32":
        for a in windows():
            is_nvidia = "nvidia" in a["ProviderName"].lower() or "ven_10de" in a["MatchingDeviceId"].lower()
            if is_nvidia:
                nvidia.append(Gpu(a["DriverDesc"] or "NVIDIA graphics card", windows_driver(a["DriverVersion"]), source="registry"))
            elif a["DriverDesc"]:
                others.append(a["DriverDesc"])
        problem = "its driver does not answer (nvidia-smi is missing or failed): install or reinstall the NVIDIA driver"
    elif sys.platform.startswith("linux"):
        for a in linux():
            if a["vendor"] == NVIDIA_VENDOR:
                nvidia.append(Gpu(f"NVIDIA graphics card (device {a['device']})", source="pci"))
                if a["driver"] == "nouveau":
                    problem = "it runs the open-source nouveau driver: PyTorch needs NVIDIA's own driver"
                elif not a["driver"]:
                    problem = "no driver is loaded for it: install NVIDIA's driver"
            else:
                others.append({"0x1002": "AMD", "0x8086": "Intel", "0x1af4": "virtual"}.get(a["vendor"], a["vendor"]) + " graphics")
        problem = problem or "nvidia-smi does not answer: install or reinstall NVIDIA's driver"
    return nvidia, others, problem


def detect(smi=from_smi, hardware=_hardware) -> GpuReport:
    if sys.platform == "darwin":
        return GpuReport("apple")
    gpus, exe, smi_problem = smi()
    if gpus:
        return GpuReport("nvidia", gpus, exe)
    cards, others, problem = hardware()
    if cards:
        if smi_problem and "nouveau" not in problem:
            problem = f"{problem} (nvidia-smi said: {smi_problem})"
        return GpuReport("no-driver", cards, problem=problem, others=others)
    return GpuReport("none", others=others)


if __name__ == "__main__":       # python -m atlas_toolkit.core.gpu: what this computer has
    r = detect()
    print(r.status, "-", r.summary())
    for g in r.gpus:
        print(f"  {g}")
